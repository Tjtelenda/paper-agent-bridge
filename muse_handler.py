"""Fixed bridge hook that hands a compact capture to the local Muse gadget socket."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import uuid

DEFAULT_SOCKET = "/run/musegadget/musegadget.sock"
MAX_INPUT = 256 * 1024
MAX_LOCAL_REQUEST = 64 * 1024
SESSION_NAMESPACE = uuid.UUID("71deab27-5acc-5d45-90e8-a491bab61b26")


def session_id(request_id):
    return str(uuid.uuid5(SESSION_NAMESPACE, request_id))


def _asset(value):
    if not isinstance(value, dict):
        return None
    return {key: value[key] for key in ("stored_path", "sha256", "media_type", "size") if key in value}


def build_message(envelope):
    required = {"type", "request_id", "message_id", "destination", "content"}
    if not isinstance(envelope, dict) or not required <= set(envelope) or envelope["destination"] != "muse":
        raise ValueError("expected a normalized Muse bridge envelope")
    if envelope["type"] not in ("capture", "conversation", "device_reply"):
        raise ValueError("unsupported envelope type")
    if "source" in envelope:
        if not isinstance(envelope["source"], dict) or envelope["type"] == "conversation":
            raise ValueError("invalid source")
    elif envelope["type"] == "capture":
        raise ValueError("capture requires a source")
    content = envelope["content"]
    mode = content.get("mode") if isinstance(content, dict) else None
    if envelope["type"] == "conversation" and mode != "raw_text":
        raise ValueError("conversation requires text")
    if mode == "raw_text":
        submitted = {"mode": mode, "text": content.get("text")}
    elif mode == "raw_image":
        submitted = {"mode": mode, "source_asset": _asset(content.get("image"))}
    elif mode == "converted_text":
        submitted = {"mode": mode, "converted_text": content.get("text"),
                     "source_asset": _asset(content.get("source_image"))}
    else:
        raise ValueError("unsupported content mode")
    comment = envelope.get("comment")
    if isinstance(comment, dict) and comment.get("mode") == "text":
        submitted["comment"] = {"mode": "text", "text": comment.get("text")}
    elif isinstance(comment, dict) and comment.get("mode") == "ink":
        submitted["comment"] = {"mode": "ink", "asset": _asset(comment.get("image"))}
    elif comment is not None:
        raise ValueError("unsupported comment mode")
    context = {
        "request_id": envelope["request_id"], "message_id": envelope["message_id"],
        "in_reply_to": envelope.get("in_reply_to"), "type": envelope["type"],
        "submitted": submitted,
    }
    if "source" in envelope:
        context["source"] = envelope["source"]
    helper = Path(__file__).with_name("relay_client.py")
    instruction = (
        "This is an explicitly confirmed reMarkable message. Preserve its thread identity and any source "
        "reference; a source-free conversation is not a notebook capture. Answer "
        "the user's writing/comment. Local asset paths name immutable preserved files; use file.read only "
        "when the image is needed. The chat POST acknowledgement is not your answer. To return your final "
        "answer to the tablet, write it to a private UTF-8 file and run: "
        f"python3 {helper} reply --request-id {envelope['request_id']} "
        f"--in-reply-to {envelope['message_id']} --actor muse ANSWER_FILE. "
        "The helper reads its credential from private local configuration; never request or print it.\n\n"
        "Message context:\n" + json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)
    )
    encoded = json.dumps({"message": instruction, "session_id": session_id(envelope["request_id"])},
                         ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > MAX_LOCAL_REQUEST:
        raise ValueError("Muse local request exceeds 64 KiB")
    return instruction


def send_to_muse(envelope, socket_path=DEFAULT_SOCKET, timeout=15):
    message = build_message(envelope)
    request = json.dumps({"message": message, "session_id": session_id(envelope["request_id"])},
                         ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(socket_path)
        client.sendall(request)
        with client.makefile("rb") as stream:
            line = stream.readline(65537)
    if not line or len(line) > 65536 or not line.endswith(b"\n"):
        raise ValueError("invalid Muse socket response")
    reply = json.loads(line)
    if not isinstance(reply, dict):
        raise ValueError("invalid Muse socket response")
    return reply


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default=os.environ.get("MUSEGADGET_SOCKET", DEFAULT_SOCKET))
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 15:
        parser.error("timeout must be greater than zero and at most 15 seconds")
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        print("Muse envelope exceeds 256 KiB", file=sys.stderr)
        return 1
    try:
        envelope = json.loads(raw)
        reply = send_to_muse(envelope, args.socket, args.timeout)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, socket.timeout) as error:
        print(f"Muse handoff uncertain: {type(error).__name__}", file=sys.stderr)
        return 1
    if reply.get("ok") is not True:
        print("Muse did not accept the message", file=sys.stderr)
        return 1
    print(json.dumps({"accepted": True, "request_id": envelope["request_id"],
                      "session_id": session_id(envelope["request_id"]),
                      "remote_status": reply.get("status")}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
