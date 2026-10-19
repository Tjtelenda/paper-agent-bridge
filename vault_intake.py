"""Trusted raw-image storage hook; transcription and organization happen later."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import time

from agent_handler import HandlerError, MAX_INPUT, validate_envelope
from relay_client import post


def read_asset(asset, directory, limit):
    if not isinstance(asset, dict) or set(asset) != {"stored_path", "sha256", "size", "media_type"}:
        raise ValueError("invalid immutable image reference")
    path = Path(asset["stored_path"])
    root = Path(directory).resolve(strict=True)
    if not path.is_absolute() or path.parent != root:
        raise ValueError("image must be in the configured relay asset directory")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or not 0 < info.st_size <= limit or info.st_size != asset["size"]):
            raise ValueError("image ownership, type or size is invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    finally:
        os.close(descriptor)
    if len(data) != asset["size"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
        raise ValueError("immutable image digest mismatch")
    formats = {"image/png": "png", "image/jpeg": "jpeg"}
    if asset["media_type"] not in formats:
        raise ValueError("unsupported image format")
    return {"bytes": data, "sha256": asset["sha256"], "format": formats[asset["media_type"]]}


def store(envelope, writer, asset_dir):
    validate_envelope(envelope)
    if envelope["type"] != "capture" or envelope["destination"] != "obsidian":
        raise ValueError("raw intake only accepts Obsidian captures")
    content = envelope["content"]
    mode = content.get("mode")
    if mode not in ("raw_image", "converted_text"):
        raise ValueError("raw intake requires the original image")
    image = read_asset(content.get("image" if mode == "raw_image" else "source_image"),
                       asset_dir, 4 * 1024 * 1024)
    comment = envelope.get("comment") or {}
    if comment and comment.get("mode") not in ("text", "ink"):
        raise ValueError("unsupported comment")
    ink = read_asset(comment.get("image"), asset_dir, 1024 * 1024) if comment.get("mode") == "ink" else None
    provenance = {key: envelope["source"].get(key) for key in writer.RAW_CAPTURE_PROVENANCE_KEYS}
    provenance["title"] = envelope["source"].get("notebook_title")
    provenance["label"] = envelope["source"].get("page_label")
    # Source validation is shared with the relay, without creating a second database.
    from bridge import Bridge
    Bridge._source(None, envelope["source"])
    lock_path = Path(writer.LOCKFILE)
    with lock_path.open("a") as lock:
        deadline = time.monotonic() + 3
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("vault writer busy; source retained for reconciliation")
                time.sleep(0.05)
        return writer.live_write_raw_image_capture(
            capture_id=envelope["request_id"], image=image, provenance=provenance,
            actor="bridge", source="remarkable-raw-intake",
            comment_text=comment.get("text") if comment.get("mode") == "text" else None,
            comment_image=ink)


def receipt(envelope, result):
    if result.get("readback_verified") is not True or result.get("status") != "raw_stored":
        raise ValueError("writer did not verify raw storage")
    evidence = {"note_path": result["note_path"], "image_sha256": result["image_sha256"],
                "writer_revision": result["git_commit"], "readback_verified": True}
    # Stable per-capture event makes retry after a lost HTTP response safe.
    event_id = "raw_" + hashlib.sha256(envelope["request_id"].encode()).hexdigest()[:32]
    return {"event_id": event_id, "request_id": envelope["request_id"], "actor": "obsidian",
            "stage": "raw_stored", "state": "completed", "evidence": evidence}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--writer", required=True, type=Path)
    parser.add_argument("--asset-dir", required=True, type=Path)
    parser.add_argument("--relay-config-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        incoming = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(incoming) > MAX_INPUT:
            raise ValueError("oversized capture envelope")
        envelope = json.loads(incoming)
        spec = importlib.util.spec_from_file_location("sanctioned_vault_writer", args.writer)
        writer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(writer)
        result = store(envelope, writer, args.asset_dir)
        status, _ = post(args.relay_config_dir, "/v1/agent/stages", receipt(envelope, result))
        return 0 if status in (200, 201) else 1
    except (OSError, ValueError, HandlerError, TimeoutError, SystemExit):
        # Never log submitted content, image paths or credentials.
        print("Raw intake incomplete; preserve the source and reconcile the capture ID.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
