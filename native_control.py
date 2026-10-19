"""Invoke the tablet's journaled native adapter through the existing SSH connection."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from document_delivery import private_fd, save
from library_control import private_state

UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')


def ssh(host, command, data=None, timeout=40, accepted=(0,)):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', host):
        raise ValueError('Choose a configured SSH host alias')
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', host, command],
                            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode not in accepted:
        raise ValueError('Native device command failed; preserve request ID and reconcile privately')
    return result.stdout


def invoke(host, request):
    raw = json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode()
    if len(raw) > 128 * 1024:
        raise ValueError('Native request exceeds 128 KiB')
    response = ssh(host, '/home/root/xovi/goodbot/backend/control-client', raw, accepted=(0, 1, 2))
    if len(response) > 2 * 1024 * 1024:
        raise ValueError('Oversized native receipt')
    result = json.loads(response)
    if result.get('request_id') != request['request_id'] or result.get('operation') != request['operation']:
        raise ValueError('Native receipt identity mismatch')
    return result


def stage_image(host, path):
    data = Path(path).read_bytes()
    if not 0 < len(data) <= 4 * 1024 * 1024:
        raise ValueError('Image must be 1 byte–4 MiB')
    image_format = 'png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'jpeg' if data.startswith(b'\xff\xd8\xff') else None
    if image_format is None:
        raise ValueError('Only PNG/JPEG image assets are supported')
    digest = hashlib.sha256(data).hexdigest()
    # Names contain only a digest and a fixed extension; user paths never enter the shell.
    command = ('set -eu\numask 077\ndir=/home/root/.local/share/goodbot-bridge/control/assets\n'
               'mkdir -p "$dir"\n'
               f'target="$dir/{digest}.{image_format}"\n'
               'temp=$(mktemp "$dir/.upload-XXXXXX")\ntrap \'rm -f "$temp"\' EXIT\n'
               'cat > "$temp"\nif test -e "$target"; then cmp "$temp" "$target"; else ln "$temp" "$target"; fi\n')
    ssh(host, command, data)
    return digest, image_format


def backup(host, identity, directory):
    if not UUID.fullmatch(identity):
        raise ValueError('Backup requires an exact native document UUID')
    # Read only: native files are never edited. Refuse a changing snapshot.
    script = ('set -eu\ncd /home/root/.local/share/remarkable/xochitl\n'
              f'id={identity}\n'
              'test -f "$id.metadata"\ntest -f "$id.content"\n'
              'find "$id" -type f -exec sha256sum {} \\; | sort\nsha256sum "$id.metadata" "$id.content"\n')
    before = ssh(host, script)
    archive = ssh(host, 'set -eu\ncd /home/root/.local/share/remarkable/xochitl\n'
                  f'tar -cf - {identity}.metadata {identity}.content {identity}', timeout=60)
    after = ssh(host, script)
    if before != after or not archive or len(archive) > 250 * 1024 * 1024:
        raise ValueError('Native files changed during backup or exceeded limit')
    digest = hashlib.sha256(archive).hexdigest()
    path = directory / (identity + '-' + digest + '.tar')
    if not path.exists():
        with os.fdopen(private_fd(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as output:
            output.write(archive); output.flush(); os.fsync(output.fileno())
    else:
        with os.fdopen(private_fd(path, os.O_RDONLY), 'rb') as previous:
            if hashlib.sha256(previous.read()).hexdigest() != digest:
                raise ValueError('Backup collision')
    return digest, path


def _execute(host, request, state_dir, image=None, confirm_delete=False, retry_undispatched=False):
    if (not isinstance(request, dict) or set(request) != {'request_id', 'operation', 'arguments'}
            or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', str(request.get('request_id', '')))
            or not isinstance(request['arguments'], dict)):
        raise ValueError('Invalid native request envelope')
    directory = private_state(state_dir)
    receipt_path = directory / (request['request_id'] + '.json')
    identity = json.loads(json.dumps(request))
    if image:
        identity["local_image_sha256"] = hashlib.sha256(Path(image).read_bytes()).hexdigest()
    if receipt_path.exists() or receipt_path.is_symlink():
        with os.fdopen(private_fd(receipt_path, os.O_RDONLY)) as source:
            old = json.load(source)
        if old.get('request') != identity:
            raise ValueError('Request ID belongs to different input')
        if retry_undispatched and old['receipt'].get('result', {}).get('not_dispatched') is True:
            result = invoke(host, old['native_request'])
            old['receipt'] = result; save(receipt_path, old)
            return result
        return old['receipt']
    native = json.loads(json.dumps(request))
    args = native['arguments']
    if request['operation'] == 'create_notebook':
        args.setdefault('document_id', str(uuid.uuid5(uuid.NAMESPACE_URL, request['request_id'] + ':notebook')))
        args.setdefault('page_id', str(uuid.uuid5(uuid.NAMESPACE_URL, request['request_id'] + ':page')))
    if request['operation'] == 'append_page':
        args.setdefault('page_id', str(uuid.uuid5(uuid.NAMESPACE_URL, request['request_id'] + ':page')))
    if image:
        if request['operation'] != 'insert_image':
            raise ValueError('Image assets require insert_image')
        args['image_sha256'], args['image_format'] = stage_image(host, image)
    if request['operation'] in ('delete_pages', 'delete_item', 'move_pages', 'create_from_pages'):
        if not confirm_delete:
            raise ValueError('Destructive/moving-page requests require --confirm-delete and exact user scope')
        digest, _ = backup(host, args.get('id', ''), directory)
        args.update(confirmed=True, backup_sha256=digest)
    uncertain = {'request_id': request['request_id'], 'operation': request['operation'],
                 'status': 'needs_review', 'result': {'reason': 'Dispatch uncertain; reconcile the original device journal'}}
    save(receipt_path, {'request': identity, 'native_request': native, 'receipt': uncertain})
    result = invoke(host, native)
    save(receipt_path, {'request': identity, 'native_request': native, 'receipt': result})
    return result


def execute(host, request, state_dir, image=None, confirm_delete=False, retry_undispatched=False):
    request_id = request.get('request_id', '') if isinstance(request, dict) else ''
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', request_id):
        raise ValueError('Invalid request ID')
    directory = private_state(state_dir)
    with os.fdopen(private_fd(directory / (request_id + '.lock'), os.O_WRONLY | os.O_CREAT), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _execute(host, request, directory, image, confirm_delete, retry_undispatched)


def reconcile(host, request_id, state_dir):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', request_id):
        raise ValueError('Invalid request ID')
    directory = private_state(state_dir)
    path = directory / (request_id + '.json')
    with os.fdopen(private_fd(directory / (request_id + '.lock'), os.O_WRONLY | os.O_CREAT), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with os.fdopen(private_fd(path, os.O_RDONLY)) as source:
            record = json.load(source)
        query = {'request_id': 'native_status_' + uuid.uuid4().hex, 'operation': 'request_status',
                 'arguments': {'target_request_id': request_id}}
        answer = invoke(host, query)
        result = answer.get('result', {})
        if answer.get('status') != 'verified':
            return record['receipt']
        if result.get('found') is False:
            receipt = {'request_id': request_id, 'operation': record['native_request']['operation'],
                       'status': 'needs_review', 'result': {'not_dispatched': True, 'reason': 'No device journal entry; explicit same-ID retry is safe'}}
        elif result.get('receipt'):
            receipt = result['receipt']
            if receipt.get('request_id') != request_id:
                raise ValueError('Reconciliation identity mismatch')
        else:
            return record['receipt']
        record['receipt'] = receipt; save(path, record)
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request_file', type=Path)
    parser.add_argument('--ssh-host', default='remarkable')
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--confirm-delete', action='store_true')
    parser.add_argument('--reconcile', action='store_true')
    parser.add_argument('--retry-undispatched', action='store_true')
    args = parser.parse_args(argv)
    try:
        raw = args.request_file.read_bytes()
        if len(raw) > 128 * 1024:
            raise ValueError('Oversized request file')
        request = json.loads(raw)
        result = reconcile(args.ssh_host, request['request_id'], args.state_dir) if args.reconcile else execute(args.ssh_host, request, args.state_dir, args.image, args.confirm_delete, args.retry_undispatched)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get('status') == 'verified' else 2
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print(json.dumps({'status': 'needs_review', 'reason': 'Native command failed; keep its request ID and private receipts'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
