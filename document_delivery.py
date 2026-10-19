#!/usr/bin/env python3
"""Explicit PDF/EPUB delivery to the normal cloud library through an installed rmapi."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import stat
import tempfile
import zipfile
import io


def run(binary, *args):
    env = dict(os.environ)
    env.pop('RMAPI_TRACE', None)
    result = subprocess.run([binary, '-ni', *args], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=180, env=env, check=False)
    if result.returncode:
        raise ValueError('Cloud client failed; inspect authentication/connectivity privately')
    return result.stdout


def listing(binary, folder):
    nodes = json.loads(run(binary, '-json', 'ls', folder))
    if not isinstance(nodes, list) or any(not isinstance(n, dict) or
            not all(isinstance(n.get(k), str) for k in ('id', 'name', 'type', 'parent')) for n in nodes):
        raise ValueError('Unsupported cloud listing format')
    return nodes


def resolve_folder(binary, folder):
    parent, current = '', '/'
    for component in PurePosixPath(folder).parts[1:]:
        matches = [n for n in listing(binary, current)
                   if n['name'] == component and n['parent'] == parent]
        if len(matches) != 1 or matches[0]['type'] != 'CollectionType' or not matches[0]['id']:
            raise ValueError('Destination folder is missing or ambiguous; create/select it in the library first')
        parent = matches[0]['id']
        current = str(PurePosixPath(current) / component)
    return parent


def validate(file, folder, request_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', request_id):
        raise ValueError('Request ID must be 16–80 letters, digits, underscores or hyphens')
    if not folder.startswith('/') or folder.startswith('//') or folder != str(PurePosixPath(folder)) or any(
            p in ('.', '..') or any(ord(c) < 32 or c in '*?[]\\' for c in p) for p in folder.split('/')[1:]):
        raise ValueError('Use an exact absolute library folder path without traversal or glob characters')
    if file.suffix not in ('.pdf', '.epub') or not file.stem or file.stem.startswith('.') or any(ord(c) < 32 or c in '*?[]\\' for c in file.name):
        raise ValueError('Choose a named .pdf or .epub file')
    if not file.is_file() or not 0 < file.stat().st_size <= 100 * 1024 * 1024:
        raise ValueError('Document must be a regular file of at most 100 MiB')
    with file.open('rb') as stream:
        data = stream.read(100 * 1024 * 1024 + 1)
    if not 0 < len(data) <= 100 * 1024 * 1024:
        raise ValueError('Document changed or exceeds 100 MiB')
    if file.suffix == '.pdf' and not data.startswith(b'%PDF-'):
        raise ValueError('Invalid PDF signature')
    if file.suffix == '.epub':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                info = archive.getinfo('mimetype')
                if info.file_size > 100 or archive.read(info) != b'application/epub+zip':
                    raise ValueError('Invalid EPUB media type')
        except (zipfile.BadZipFile, KeyError) as error:
            raise ValueError('Invalid EPUB container') from error
    return data


def check_private_stat(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise ValueError('State entry must be an owned regular file without links')


def private_fd(path, flags):
    try:
        check_private_stat(path.lstat())
    except FileNotFoundError:
        pass
    descriptor = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    try:
        check_private_stat(os.fstat(descriptor))
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def save(path, value):
    try:
        check_private_stat(path.lstat())
    except FileNotFoundError:
        pass
    descriptor, name = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def deliver(file, folder, request_id, state_dir, binary='rmapi'):
    data = validate(file, folder, request_id)
    identity = {'request_id': request_id, 'folder': folder, 'filename': file.name,
                'sha256': hashlib.sha256(data).hexdigest()}
    if state_dir.is_symlink():
        raise ValueError('State directory cannot be a symlink')
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state_dir.stat().st_uid != os.getuid() or state_dir.stat().st_mode & 0o077:
        raise ValueError('State directory must be owned by this user with mode 700')
    receipt_path = state_dir / (request_id + '.json')
    with os.fdopen(private_fd(state_dir / (request_id + '.lock'), os.O_WRONLY | os.O_CREAT), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            receipt_fd = private_fd(receipt_path, os.O_RDONLY)
        except FileNotFoundError:
            receipt_fd = None
        if receipt_fd is not None:
            with os.fdopen(receipt_fd, 'r') as stream:
                receipt = json.load(stream)
            if receipt.get('request') != identity:
                raise ValueError('Request ID already belongs to different content or destination')
            return receipt
        executable = shutil.which(binary)
        if executable is None:
            raise ValueError('rmapi is not installed; install and pair a compatible client separately')
        parent = resolve_folder(executable, folder)
        if any(n['name'] == file.stem for n in listing(executable, folder)):
            raise ValueError('Document name already exists; replacement is not permitted')
        staged = state_dir / request_id / file.name
        staged.parent.mkdir(mode=0o700)
        staged.write_bytes(data)
        staged.chmod(0o600)
        receipt = {'request': identity, 'folder_id': parent, 'status': 'dispatch_uncertain',
                   'device_sync_verified': False}
        # Persist before put: a crash/timeout must never cause an automatic second upload.
        save(receipt_path, receipt)
        try:
            run(executable, 'put', str(staged.resolve()), folder)
            if resolve_folder(executable, folder) != parent:
                raise ValueError('Destination changed during delivery')
            matches = [n for n in listing(executable, folder)
                       if n['name'] == file.stem and n['parent'] == parent and n['type'] == 'DocumentType']
            if len(matches) != 1 or not matches[0]['id']:
                raise ValueError('Cloud read-back could not identify the document')
            receipt.update(status='cloud_visible', document_id=matches[0]['id'])
        except (ValueError, OSError, subprocess.SubprocessError):
            receipt['detail'] = 'Upload or read-back uncertain; reconcile in the cloud library before any new request'
        save(receipt_path, receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--folder', required=True)
    parser.add_argument('--request-id', required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--rmapi', default='rmapi', help='Installed compatible rmapi binary')
    args = parser.parse_args()
    try:
        receipt = deliver(args.file, args.folder, args.request_id, args.state_dir, args.rmapi)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Delivery refused: {error}\n')
    print(json.dumps(receipt, indent=2))
    return 0 if receipt['status'] == 'cloud_visible' else 2


if __name__ == '__main__':
    raise SystemExit(main())
