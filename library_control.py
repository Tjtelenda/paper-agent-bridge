"""Explicit, identity-checked cloud library operations through the paired rmapi."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile

from document_delivery import listing, private_fd, resolve_folder, run, save


def library_path(value, root=True):
    if (not isinstance(value, str) or not value.startswith('/') or value.startswith('//')
            or value != str(PurePosixPath(value)) or (not root and value == '/')
            or any(part in ('.', '..') or any(ord(c) < 32 or c in '*?[]\\' for c in part)
                   for part in value.split('/')[1:])):
        raise ValueError('Use an exact absolute library path without traversal or glob characters')
    return value


def entry(binary, path):
    library_path(path, root=False)
    parent, name = str(PurePosixPath(path).parent), PurePosixPath(path).name
    parent_id = resolve_folder(binary, parent)
    matches = [node for node in listing(binary, parent)
               if node['parent'] == parent_id and node['name'].casefold() == name.casefold()]
    if len(matches) != 1 or matches[0]['name'] != name or not matches[0]['id']:
        raise ValueError('Library source is missing, case-mismatched or ambiguous')
    return matches[0]


def private_state(directory):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('State directory cannot be a symlink')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('State directory must be owned and mode 700')
    return directory


def mutate(binary, *, operation, target, request_id, state_dir, source=None, expected_id=None,
           expected_version=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', request_id):
        raise ValueError('Request ID must be 16–80 safe characters')
    library_path(target, root=False)
    if operation not in ('create-folder', 'move'):
        raise ValueError('Unsupported library mutation')
    if operation == 'move':
        library_path(source, root=False)
        if not isinstance(expected_id, str) or not expected_id:
            raise ValueError('Move requires the source ID from a fresh inspect')
        if target.startswith(source + '/'):
            raise ValueError('Cannot move an item into its own descendants')
    identity = {'operation': operation, 'source': source, 'target': target,
                'expected_id': expected_id, 'expected_version': expected_version}
    directory = private_state(state_dir)
    receipt_path = directory / (request_id + '.json')
    with os.fdopen(private_fd(directory / 'library.lock', os.O_WRONLY | os.O_CREAT), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if receipt_path.exists() or receipt_path.is_symlink():
            with os.fdopen(private_fd(receipt_path, os.O_RDONLY)) as handle:
                previous = json.load(handle)
            if previous.get('request') != identity:
                raise ValueError('Request ID belongs to a different operation')
            return previous
        parent = str(PurePosixPath(target).parent)
        parent_id = resolve_folder(binary, parent)
        name = PurePosixPath(target).name
        collisions = [node for node in listing(binary, parent)
                      if node['parent'] == parent_id and node['name'].casefold() == name.casefold()]
        original = entry(binary, source) if operation == 'move' else None
        if original and (original['id'] != expected_id or
                         (expected_version is not None and original.get('version') != expected_version)):
            raise ValueError('Source identity or version changed; inspect again')
        if collisions:
            if original and target == source and len(collisions) == 1 and collisions[0]['id'] == expected_id:
                result = {'request': identity, 'status': 'cloud_verified', 'entry': original,
                          'no_change': True, 'device_sync_verified': False}
                save(receipt_path, result)
                return result
            raise ValueError('Destination name exists or is ambiguous; replacement is forbidden')
        result = {'request': identity, 'status': 'dispatch_uncertain', 'device_sync_verified': False}
        # Persist before the single attempt. A timeout or lost response never causes replay.
        save(receipt_path, result)
        try:
            if resolve_folder(binary, parent) != parent_id:
                raise ValueError('Destination folder changed')
            if original and entry(binary, source) != original:
                raise ValueError('Source changed before dispatch')
            run(binary, 'mkdir', target) if operation == 'create-folder' else run(binary, 'mv', source, target)
            saved = entry(binary, target)
            if saved['parent'] != parent_id or (original and saved['id'] != expected_id):
                raise ValueError('Cloud read-back identity mismatch')
            if operation == 'create-folder' and saved['type'] != 'CollectionType':
                raise ValueError('Created item is not a folder')
            if original:
                old_parent = str(PurePosixPath(source).parent)
                if any(node['id'] == expected_id and str(PurePosixPath(old_parent) / node['name']) != target
                       for node in listing(binary, old_parent)):
                    raise ValueError('Old source is still present')
            result.update(status='cloud_verified', entry=saved)
            save(receipt_path, result)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            # Keep original identity and uncertainty; raw cloud diagnostics may contain private data.
            pass
        return result


def download(binary, path, expected_id, output):
    if not isinstance(expected_id, str) or not re.fullmatch(r'[0-9a-fA-F-]{36}', expected_id):
        raise ValueError('Download requires a cloud document UUID')
    node = entry(binary, path)
    if node['id'] != expected_id or node['type'] != 'DocumentType':
        raise ValueError('Download requires the exact document ID from inspect')
    output = private_state(output)
    with tempfile.TemporaryDirectory(prefix='.download-', dir=output) as temporary:
        environment = dict(os.environ)
        environment.pop('RMAPI_TRACE', None)
        process = subprocess.run([binary, '-ni', 'get', '--id', expected_id], cwd=temporary,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=180, env=environment)
        files = list(Path(temporary).iterdir())
        if process.returncode or len(files) != 1:
            raise ValueError('Cloud download failed or returned unexpected files')
        source = files[0]
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 250 * 1024 * 1024:
            raise ValueError('Invalid downloaded archive')
        if entry(binary, path) != node:
            raise ValueError('Source changed during download')
        # A fresh, content-addressed private backup never replaces an earlier download.
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        target = output / (expected_id + '-' + digest + '.rmdoc')
        if target.exists() or target.is_symlink():
            with os.fdopen(private_fd(target, os.O_RDONLY), 'rb') as handle:
                if handle.read(len(data) + 1) != data:
                    raise ValueError('Backup target exists with different contents')
        else:
            descriptor = private_fd(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        return {'status': 'downloaded', 'id': expected_id, 'path': str(target),
                'sha256': digest, 'size': len(data), 'format': 'rmdoc'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rmapi', default='rmapi')
    commands = parser.add_subparsers(dest='operation', required=True)
    for name in ('list', 'inspect'):
        command = commands.add_parser(name)
        command.add_argument('path')
    for name in ('create-folder', 'move'):
        command = commands.add_parser(name)
        if name == 'move':
            command.add_argument('source')
            command.add_argument('--expected-id', required=True)
            command.add_argument('--expected-version', type=int)
        command.add_argument('target')
        command.add_argument('--request-id', required=True)
        command.add_argument('--state-dir', type=Path, required=True)
    command = commands.add_parser('download')
    command.add_argument('path')
    command.add_argument('--expected-id', required=True)
    command.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        binary = shutil.which(args.rmapi)
        if binary is None:
            raise ValueError('Paired cloud client is unavailable')
        if args.operation == 'list':
            library_path(args.path)
            parent = resolve_folder(binary, args.path)
            result = [node for node in listing(binary, args.path) if node['parent'] == parent]
        elif args.operation == 'inspect':
            result = entry(binary, args.path)
        elif args.operation == 'download':
            result = download(binary, args.path, args.expected_id, args.output_dir)
        else:
            result = mutate(binary, operation=args.operation, target=args.target, request_id=args.request_id,
                            state_dir=args.state_dir, source=getattr(args, 'source', None),
                            expected_id=getattr(args, 'expected_id', None),
                            expected_version=getattr(args, 'expected_version', None))
        print(json.dumps(result, ensure_ascii=False))
        return 2 if isinstance(result, dict) and result.get('status') == 'dispatch_uncertain' else 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print(json.dumps({'error': 'Library operation failed; inspect paths, identity and private receipts'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
