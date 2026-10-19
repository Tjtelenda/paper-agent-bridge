"""Queue native notebook commands through the authenticated relay while the tablet is away."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, Request

from document_delivery import private_fd
from relay_client import load_config, NoRedirect

MAX_REQUEST = 128 * 1024
MAX_RESPONSE = 2 * 1024 * 1024 + 4096


def exchange(config_dir, endpoint, payload=None):
    url, token = load_config(config_dir)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    request = Request(url + endpoint, data=data,
                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        response = build_opener(NoRedirect).open(request, timeout=25)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError('Oversized native control response')
        return response.code, json.loads(raw)


def status(config_dir, request_id):
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', request_id):
        raise ValueError('Invalid request ID')
    return exchange(config_dir, '/v1/agent/control/requests/' + request_id)


def upload_image(config_dir, image):
    with os.fdopen(private_fd(image, os.O_RDONLY), 'rb') as source:
        data = source.read(4 * 1024 * 1024 + 1)
    image_format = 'png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'jpeg' if data.startswith(b'\xff\xd8\xff') else None
    if not image_format or len(data) > 4 * 1024 * 1024:
        raise ValueError('Image must be a PNG/JPEG no larger than 4 MiB')
    code, result = exchange(config_dir, '/v1/agent/control/assets', {
        'image': {'media_type': 'image/' + image_format, 'data_base64': base64.b64encode(data).decode()}})
    digest = hashlib.sha256(data).hexdigest()
    if code != 201 or result.get('image_sha256') != digest or result.get('image_format') != image_format:
        raise ValueError('Image upload was not verified')
    return digest, image_format


def submit(config_dir, request, wait=0):
    # Same-ID submission is safe after a lost HTTP response: the relay and device both journal it.
    code, accepted = exchange(config_dir, '/v1/agent/control/requests', request)
    if code not in (200, 201):
        return code, accepted
    request_id = request.get('request_id')
    if accepted.get('accepted') is not True or accepted.get('request_id') != request_id:
        raise ValueError('Native queue acknowledgement identity mismatch')
    deadline = time.monotonic() + wait
    while True:
        code, result = status(config_dir, request_id)
        if code != 200 or result.get('status') == 'completed' or time.monotonic() >= deadline:
            return code, result
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-dir', default=os.environ.get('REMARKABLE_RELAY_CONFIG_DIR',
                        str(Path.home() / '.config/goodbot2-remarkable-relay')))
    actions = parser.add_subparsers(dest='action', required=True)
    send = actions.add_parser('submit')
    send.add_argument('request_file', type=Path)
    send.add_argument('--wait', type=int, choices=range(0, 61), default=0, metavar='0..60')
    check = actions.add_parser('status')
    check.add_argument('request_id')
    upload = actions.add_parser('upload-image')
    upload.add_argument('image', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == 'status':
            code, result = status(args.config_dir, args.request_id)
        elif args.action == 'upload-image':
            digest, image_format = upload_image(args.config_dir, args.image)
            code, result = 201, {'image_sha256': digest, 'image_format': image_format}
        else:
            with os.fdopen(private_fd(args.request_file, os.O_RDONLY), 'rb') as source:
                raw = source.read(MAX_REQUEST + 1)
            if not raw or len(raw) > MAX_REQUEST:
                raise ValueError('Command must contain 1 byte–128 KiB')
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError('Command must be an object')
            code, result = submit(args.config_dir, request, args.wait)
        print(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
        if code not in (200, 201):
            return 1
        if result.get('status') == 'completed':
            return 0 if (result.get('receipt') or {}).get('status') == 'verified' else 2
        return 0  # Queued/dispatched is explicitly reported; neither means the operation completed.
    except (OSError, ValueError, UnicodeError, RecursionError, URLError):
        print('Native relay request failed. Preserve the request ID and query its status before retrying.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
