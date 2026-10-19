"""Trusted local helper for posting agent replies and stage receipts to the bridge."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{16,80}")
ACTORS = ("muse", "codex", "obsidian", "hermes")
MAX_FILE = 64 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def load_config(directory):
    directory = Path(directory)
    url = (directory / "url").read_text().strip().rstrip("/")
    token_path = directory / "token"
    if stat.S_IMODE(token_path.stat().st_mode) & 0o077:
        raise ValueError("relay token file must be owner-only")
    token = token_path.read_text().strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"):
        raise ValueError("relay URL must use HTTPS or authenticated loopback HTTP")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("relay URL must be an origin without credentials, path, query, or fragment")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("invalid relay token")
    return url, token


def read_file(path):
    data = Path(path).read_bytes()
    if not data or len(data) > MAX_FILE:
        raise ValueError("input file must contain 1–65536 bytes")
    return data


def post(config_dir, endpoint, payload):
    url, token = load_config(config_dir)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    request = Request(url + endpoint, data=data, method="POST",
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect).open(request, timeout=20) as response:
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise ValueError("oversized relay response")
            return response.status, json.loads(body)
    except HTTPError as error:
        body = error.read(1024 * 1024 + 1)
        return error.code, json.loads(body) if len(body) <= 1024 * 1024 else {"error": "oversized response"}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default=os.environ.get(
        "REMARKABLE_RELAY_CONFIG_DIR", str(Path.home() / ".config/goodbot2-remarkable-relay")))
    subparsers = parser.add_subparsers(dest="action", required=True)
    reply = subparsers.add_parser("reply")
    reply.add_argument("--request-id", required=True)
    reply.add_argument("--in-reply-to", required=True)
    reply.add_argument("--actor", choices=ACTORS, required=True)
    reply.add_argument("text_file")
    stage = subparsers.add_parser("stage")
    stage.add_argument("json_file")
    args = parser.parse_args(argv)
    try:
        if args.action == "reply":
            if not IDENTIFIER.fullmatch(args.request_id) or not IDENTIFIER.fullmatch(args.in_reply_to):
                raise ValueError("invalid request or parent message ID")
            text = read_file(args.text_file).decode("utf-8")
            digest = hashlib.sha256((args.request_id + "\0" + args.in_reply_to + "\0" +
                                     args.actor + "\0" + text).encode()).hexdigest()[:32]
            payload = {"message_id": "relay_" + digest, "request_id": args.request_id,
                       "in_reply_to": args.in_reply_to, "actor": args.actor, "text": text}
            status, receipt = post(args.config_dir, "/v1/agent/replies", payload)
        else:
            payload = json.loads(read_file(args.json_file))
            status, receipt = post(args.config_dir, "/v1/agent/stages", payload)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"Relay post failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
