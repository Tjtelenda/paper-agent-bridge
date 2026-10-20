"""Local development relay for reMarkable captures, replies, and truthful receipts."""
import argparse
import base64
import binascii
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from agent_handler import HandlerError, build_prompt
from muse_handler import build_message

MAX_BODY = 8 * 1024 * 1024
MAX_SOURCE_IMAGE = 4 * 1024 * 1024
MAX_COMMENT_IMAGE = 1024 * 1024
MAX_TEXT = 16000
MAX_COMMENT = 4000
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{16,80}")
SOURCE_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,160}")
DESTINATIONS = ("muse", "codex", "obsidian", "hermes")
STAGES = ("raw_stored", "parsed", "stored", "organized_recorded")
STAGE_STATES = ("pending", "in_progress", "needs_review", "completed", "failed")
CONTROL_LIMIT = 128 * 1024
CONTROL_OPERATIONS = frozenset(("inspect", "list_templates", "list_folder", "create_notebook",
    "create_folder", "rename", "move_item", "append_page", "set_template", "copy_pages",
    "move_pages", "delete_pages", "quick_sheet", "insert_text", "insert_image", "trash",
    "restore", "delete_item", "open_page", "create_from_pages", "copy_item", "list_tags",
    "add_tags", "remove_tags", "request_status"))


class InvalidPayload(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def exact_keys(value, required, optional=()):
    return (isinstance(value, dict) and set(required) <= set(value)
            and set(value) <= set(required) | set(optional))


def valid_id(value):
    return isinstance(value, str) and bool(IDENTIFIER.fullmatch(value))


def valid_text(value, limit=MAX_TEXT):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def valid_vault_path(value):
    if not valid_text(value, 500) or value.startswith("/") or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (path.suffix == ".md" and ".." not in path.parts and len(path.parts) > 1
            and path.parts[0] not in ("90-mirrors", ".obsidian"))


class Bridge:
    def __init__(self, database, commands):
        self.database = Path(database)
        self.asset_dir = self.database.parent / "assets"
        self.asset_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.commands = commands
        self.events_changed = threading.Condition()
        for target, command in commands.items():
            if target not in DESTINATIONS or not isinstance(command, list) or not command:
                raise ValueError("commands must map known destinations to nonempty argv lists")
            if not all(isinstance(arg, str) and arg for arg in command):
                raise ValueError("command arguments must be nonempty strings")
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS threads (
                    request_id TEXT PRIMARY KEY,
                    destination TEXT NOT NULL,
                    source TEXT NOT NULL,
                    original TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL REFERENCES threads(request_id),
                    sender TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stage_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL REFERENCES threads(request_id),
                    stage TEXT NOT NULL,
                    state TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    payload_hash TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inbox_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    request_id TEXT NOT NULL REFERENCES threads(request_id),
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS control_requests (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    receipt TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _decode_image(self, value, limit):
        if not exact_keys(value, ("media_type", "data_base64")):
            raise InvalidPayload("image must contain media_type and data_base64")
        media_type = value["media_type"]
        if media_type not in ("image/png", "image/jpeg") or not isinstance(value["data_base64"], str):
            raise InvalidPayload("image must be PNG or JPEG")
        try:
            data = base64.b64decode(value["data_base64"], validate=True)
        except (ValueError, binascii.Error):
            raise InvalidPayload("invalid base64 image")
        if not data or len(data) > limit:
            raise InvalidPayload("image exceeds its size limit")
        if media_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise InvalidPayload("invalid PNG signature")
        if media_type == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
            raise InvalidPayload("invalid JPEG signature")
        return media_type, data

    def _store_image(self, value, limit):
        media_type, data = self._decode_image(value, limit)
        digest = hashlib.sha256(data).hexdigest()
        path = self.asset_dir / (digest + (".png" if media_type == "image/png" else ".jpg"))
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise InvalidPayload("stored source asset failed its digest check")
        else:
            with tempfile.NamedTemporaryFile(dir=self.asset_dir, delete=False) as output:
                output.write(data)
                temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            try:
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return {"media_type": media_type, "sha256": digest, "size": len(data), "stored_path": str(path)}

    def _source(self, value):
        required = ("notebook_id", "page_id", "revision", "selection", "captured_by", "captured_at")
        optional = ("notebook_title", "page_label", "crop")
        if not exact_keys(value, required, optional):
            raise InvalidPayload("invalid source reference")
        for key in ("notebook_id", "page_id", "revision"):
            if not isinstance(value[key], str) or not SOURCE_IDENTIFIER.fullmatch(value[key]):
                raise InvalidPayload("invalid source identifier")
        if value["captured_by"] != "remarkable":
            raise InvalidPayload("captured_by must identify the reMarkable source")
        try:
            captured_at = datetime.fromisoformat(value["captured_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            raise InvalidPayload("captured_at must be an ISO timestamp")
        if captured_at.tzinfo is None:
            raise InvalidPayload("captured_at must include a timezone offset")
        for key in ("notebook_title", "page_label"):
            if key in value and not valid_text(value[key], 300):
                raise InvalidPayload("invalid source label")
        if value["selection"] not in ("page", "crop"):
            raise InvalidPayload("selection must be page or crop")
        result = dict(value)
        if value["selection"] == "crop":
            crop = value.get("crop")
            if not exact_keys(crop, ("x", "y", "width", "height")):
                raise InvalidPayload("crop requires normalized x, y, width, and height")
            if not all(isinstance(crop[key], (int, float)) and not isinstance(crop[key], bool) for key in crop):
                raise InvalidPayload("crop coordinates must be numbers")
            x, y, width, height = (float(crop[key]) for key in ("x", "y", "width", "height"))
            if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 - x and 0 < height <= 1 - y):
                raise InvalidPayload("crop must fit within the page")
            result["crop"] = {"x": x, "y": y, "width": width, "height": height}
        elif "crop" in value:
            raise InvalidPayload("page selection cannot include crop")
        return result

    def _content(self, value):
        if not isinstance(value, dict) or value.get("mode") not in ("raw_image", "raw_text", "converted_text"):
            raise InvalidPayload("invalid content mode")
        mode = value["mode"]
        if mode == "raw_image" and exact_keys(value, ("mode", "image")):
            return {"mode": mode, "image": self._store_image(value["image"], MAX_SOURCE_IMAGE)}
        if mode == "raw_text" and exact_keys(value, ("mode", "text")) and valid_text(value["text"]):
            return dict(value)
        if mode == "converted_text" and exact_keys(value, ("mode", "text", "source_image")) and valid_text(value["text"]):
            return {"mode": mode, "text": value["text"],
                    "source_image": self._store_image(value["source_image"], MAX_SOURCE_IMAGE)}
        raise InvalidPayload("content does not match its mode")

    def _comment(self, value):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise InvalidPayload("invalid comment")
        if value.get("mode") == "text" and exact_keys(value, ("mode", "text")) and valid_text(value["text"], MAX_COMMENT):
            return dict(value)
        if value.get("mode") == "ink" and exact_keys(value, ("mode", "image")):
            return {"mode": "ink", "image": self._store_image(value["image"], MAX_COMMENT_IMAGE)}
        raise InvalidPayload("comment must be typed text or an ink image")

    def _latest_stages(self, db, request_id):
        rows = db.execute("""
            SELECT s.stage, s.state, s.evidence, s.created_at
            FROM stage_events s
            JOIN (SELECT stage, MAX(sequence) sequence FROM stage_events
                  WHERE request_id=? GROUP BY stage) latest ON latest.sequence=s.sequence
            ORDER BY s.sequence
        """, (request_id,)).fetchall()
        return {row["stage"]: {"state": row["state"], "evidence": json.loads(row["evidence"]),
                                "updated_at": row["created_at"]} for row in rows}

    def _receipt(self, db, request_id, duplicate=False):
        message = db.execute("SELECT status FROM messages WHERE message_id=?", (request_id,)).fetchone()
        return {"request_id": request_id, "status": message["status"], "duplicate": duplicate,
                "stages": self._latest_stages(db, request_id)}

    def submit(self, payload):
        if not exact_keys(payload, ("request_id", "destination", "source", "content"), ("comment",)):
            return 400, {"error": "expected request_id, destination, source, content, and optional comment"}
        request_id, destination = payload["request_id"], payload["destination"]
        if not valid_id(request_id):
            return 400, {"error": "invalid request_id"}
        if destination not in DESTINATIONS:
            return 400, {"error": "invalid destination"}
        if destination not in self.commands:
            return 503, {"error": "destination not configured; nothing accepted or dispatched"}
        try:
            source = self._source(payload["source"])
            content = self._content(payload["content"])
            comment = self._comment(payload.get("comment"))
            payload_hash = hashlib.sha256(canonical(payload).encode()).hexdigest()
        except InvalidPayload as error:
            return 400, {"error": str(error)}
        except UnicodeError:
            return 400, {"error": "payload must be valid Unicode"}
        normalized = {"type": "capture", "request_id": request_id, "message_id": request_id,
                      "destination": destination, "source": source, "content": content}
        if comment is not None:
            normalized["comment"] = comment
        return self._accept_thread(normalized, payload_hash)

    def conversation(self, payload):
        if not exact_keys(payload, ("request_id", "destination", "content")):
            return 400, {"error": "expected request_id, destination, and text content"}
        request_id, destination, content = (payload[key] for key in ("request_id", "destination", "content"))
        if not valid_id(request_id) or destination not in ("muse", "hermes", "codex"):
            return 400, {"error": "invalid conversation request_id or destination"}
        if (not exact_keys(content, ("mode", "text")) or content["mode"] != "raw_text"
                or not valid_text(content["text"])):
            return 400, {"error": "conversation requires nonempty raw_text of at most 16000 characters"}
        if destination not in self.commands:
            return 503, {"error": "destination not configured; nothing accepted or dispatched"}
        normalized = {"type": "conversation", "request_id": request_id, "message_id": request_id,
                      "destination": destination, "content": dict(content)}
        return self._accept_thread(normalized, hashlib.sha256(canonical(payload).encode()).hexdigest())

    def _accept_thread(self, normalized, payload_hash):
        request_id, destination, content = (normalized[key] for key in ("request_id", "destination", "content"))
        source = normalized.get("source")
        created = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload_hash FROM messages WHERE message_id=?", (request_id,)).fetchone()
            if row:
                if row["payload_hash"] != payload_hash:
                    return 409, {"error": "request_id already used for different content"}
                return 200, self._receipt(db, request_id, True)
            if source is None:
                error = self._conversation_preflight(normalized)
                if error:
                    return error
            db.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                       (request_id, destination, canonical(source), canonical(content), created))
            db.execute("INSERT INTO messages(message_id,request_id,sender,payload_hash,payload,status,created_at) "
                       "VALUES (?, ?, 'device', ?, ?, 'dispatching', ?)",
                       (request_id, request_id, payload_hash, canonical(normalized), created))
            initial = [("received", "completed", {"message_id": request_id})]
            if source is not None:
                initial.append(("source_preserved", "completed", self._client_original(request_id, content)))
            if destination == "obsidian":
                initial.extend((stage, "pending", {}) for stage in STAGES)
            for index, (stage, state, evidence) in enumerate(initial):
                db.execute("INSERT INTO stage_events(event_id,request_id,stage,state,evidence,created_at) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (f"system_{request_id}_{index}", request_id, stage, state, canonical(evidence), created))
            kind = normalized["type"] + "_accepted"
            accepted = {"kind": kind, "request_id": request_id, "destination": destination,
                        "source": source, "original": self._client_original(request_id, content),
                        "stages": self._latest_stages(db, request_id), "created_at": created}
            db.execute("INSERT INTO inbox_events(kind,request_id,payload,created_at) VALUES (?,?,?,?)",
                       (kind, request_id, canonical(accepted), created))
        self._notify_events()
        self._dispatch(request_id, normalized)
        with self.connect() as db:
            return 201, self._receipt(db, request_id)

    def _dispatch(self, message_id, envelope):
        status = "dispatch_uncertain"
        try:
            result = subprocess.run(self.commands[envelope["destination"]], input=canonical(envelope).encode(),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
                                    env={k: v for k, v in os.environ.items()
                                         if k not in ("REMARKABLE_TOKEN", "REMARKABLE_RELAY_TOKEN")})
            if result.returncode == 0:
                status = "handed_off"
        except (OSError, subprocess.TimeoutExpired):
            pass
        with self.connect() as db:
            db.execute("UPDATE messages SET status=? WHERE message_id=?", (status, message_id))
            request_id = envelope["request_id"]
            db.execute("INSERT INTO stage_events(event_id,request_id,stage,state,evidence,created_at) "
                       "VALUES (?, ?, 'relay_handoff', ?, ?, ?)",
                       (f"system_{message_id}_handoff", request_id,
                        "completed" if status == "handed_off" else "needs_review",
                        canonical({"message_id": message_id, "status": status,
                                   "meaning": "hook accepted" if status == "handed_off" else "effect uncertain"}), now()))
            handoff = {"kind": "handoff_update", "request_id": request_id, "message_id": message_id,
                       "status": status, "stages": self._latest_stages(db, request_id), "created_at": now()}
            db.execute("INSERT INTO inbox_events(kind,request_id,payload,created_at) VALUES ('handoff_update',?,?,?)",
                       (request_id, canonical(handoff), handoff["created_at"]))
        self._notify_events()
        return status

    def device_reply(self, payload):
        if not exact_keys(payload, ("message_id", "request_id", "content"), ("comment", "in_reply_to")):
            return 400, {"error": "invalid device reply"}
        message_id, request_id = payload["message_id"], payload["request_id"]
        if not valid_id(message_id) or not valid_id(request_id):
            return 400, {"error": "invalid message or request ID"}
        with self.connect() as db:
            thread = db.execute("SELECT * FROM threads WHERE request_id=?", (request_id,)).fetchone()
        if not thread:
            return 404, {"error": "unknown request thread"}
        if thread["destination"] not in self.commands:
            return 503, {"error": "destination not configured; nothing accepted or dispatched"}
        if "in_reply_to" in payload and not valid_id(payload["in_reply_to"]):
            return 400, {"error": "invalid in_reply_to"}
        try:
            content = self._content(payload["content"])
            comment = self._comment(payload.get("comment"))
        except InvalidPayload as error:
            return 400, {"error": str(error)}
        payload_json = canonical(payload)
        payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
        envelope = {"type": "device_reply", "message_id": message_id, "request_id": request_id,
                    "in_reply_to": payload.get("in_reply_to", request_id), "destination": thread["destination"],
                    "content": content}
        source = json.loads(thread["source"])
        if source is not None:
            envelope["source"] = source
        if comment is not None:
            envelope["comment"] = comment
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload_hash,status FROM messages WHERE message_id=?", (message_id,)).fetchone()
            if row:
                if row["payload_hash"] != payload_hash:
                    return 409, {"error": "message_id already used for different content"}
                return 200, {"message_id": message_id, "request_id": request_id,
                             "status": row["status"], "duplicate": True}
            parent = db.execute("SELECT 1 FROM messages WHERE message_id=? AND request_id=?",
                                (envelope["in_reply_to"], request_id)).fetchone()
            if not parent:
                return 400, {"error": "in_reply_to is not in this request thread"}
            if source is None and thread["destination"] == "codex":
                envelope["history"] = self._conversation_history(db, request_id)
            if source is None:
                error = self._conversation_preflight(envelope)
                if error:
                    return error
            db.execute("INSERT INTO messages(message_id,request_id,sender,payload_hash,payload,status,created_at) "
                       "VALUES (?, ?, 'device', ?, ?, 'dispatching', ?)",
                       (message_id, request_id, payload_hash, canonical(envelope), now()))
        status = self._dispatch(message_id, envelope)
        return 201, {"message_id": message_id, "request_id": request_id, "in_reply_to": envelope["in_reply_to"],
                     "status": status, "duplicate": False}

    def _conversation_preflight(self, envelope):
        while True:
            try:
                if envelope["destination"] == "muse":
                    build_message(envelope)
                else:
                    build_prompt(envelope, None)  # Non-Obsidian prompts do not read configuration.
                return None
            except (HandlerError, ValueError) as error:
                oversized = "exceeds 64 KiB" in str(error)
                history = envelope.get("history")
                if oversized and history and history["messages"]:
                    history["messages"].pop(0)
                    history["truncated"] = True
                    continue
                return (413 if oversized else 400), {"error": str(error)}

    def _conversation_history(self, db, request_id):
        rows = db.execute("SELECT message_id,sender,payload FROM messages WHERE request_id=? "
                          "ORDER BY sequence DESC LIMIT 13", (request_id,)).fetchall()
        messages, remaining, truncated = [], 12000, len(rows) > 12
        for row in rows[:12]:
            payload = json.loads(row["payload"])
            content = payload.get("content", {})
            text = payload.get("text") or content.get("text") or "[Image omitted from text history]"
            comment = payload.get("comment", {})
            if comment.get("mode") == "text":
                text += "\nComment: " + comment["text"]
            if not remaining:
                truncated = True
                break
            if len(text) > remaining:
                text = text[:remaining]
                truncated = True
            remaining -= len(text)
            messages.append({"message_id": row["message_id"], "sender": row["sender"], "text": text})
        return {"messages": list(reversed(messages)), "truncated": truncated}

    def _client_original(self, request_id, content):
        mode = content["mode"]
        if mode == "raw_text":
            return {"mode": mode, "text": content["text"]}
        image = content["image"] if mode == "raw_image" else content["source_image"]
        result = {"mode": mode, "media_type": image["media_type"], "size": image["size"],
                  "sha256": image["sha256"], "preview_url": f"/v1/sources/{request_id}"}
        if mode == "converted_text":
            result["converted_text"] = content["text"]
        return result

    def _client_payload(self, value):
        if isinstance(value, dict):
            return {key: self._client_payload(item) for key, item in value.items() if key != "stored_path"}
        if isinstance(value, list):
            return [self._client_payload(item) for item in value]
        return value

    def agent_reply(self, payload):
        if not exact_keys(payload, ("message_id", "request_id", "actor", "text"), ("in_reply_to",)):
            return 400, {"error": "invalid agent reply"}
        message_id, request_id = payload["message_id"], payload["request_id"]
        if (not valid_id(message_id) or not valid_id(request_id) or payload["actor"] not in DESTINATIONS
                or not valid_text(payload["text"])):
            return 400, {"error": "invalid agent reply fields"}
        in_reply_to = payload.get("in_reply_to", request_id)
        if not valid_id(in_reply_to):
            return 400, {"error": "invalid in_reply_to"}
        payload_json = canonical(payload)
        payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
        created = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            thread = db.execute("SELECT * FROM threads WHERE request_id=?", (request_id,)).fetchone()
            if not thread:
                return 404, {"error": "unknown request thread"}
            row = db.execute("SELECT payload_hash,status FROM messages WHERE message_id=?", (message_id,)).fetchone()
            if row:
                if row["payload_hash"] != payload_hash:
                    return 409, {"error": "message_id already used for different content"}
                return 200, {"message_id": message_id, "request_id": request_id,
                             "status": row["status"], "duplicate": True}
            if not db.execute("SELECT 1 FROM messages WHERE message_id=? AND request_id=?",
                              (in_reply_to, request_id)).fetchone():
                return 400, {"error": "in_reply_to is not in this request thread"}
            message = {"message_id": message_id, "request_id": request_id, "in_reply_to": in_reply_to,
                       "actor": payload["actor"], "text": payload["text"], "created_at": created}
            db.execute("INSERT INTO messages(message_id,request_id,sender,payload_hash,payload,status,created_at) "
                       "VALUES (?, ?, ?, ?, ?, 'queued_for_device', ?)",
                       (message_id, request_id, "relay:" + payload["actor"], payload_hash, canonical(message), created))
            event = {"kind": "agent_reply", "message": message, "source": json.loads(thread["source"]),
                     "original": self._client_original(request_id, json.loads(thread["original"]))}
            db.execute("INSERT INTO inbox_events(kind,request_id,payload,created_at) VALUES ('agent_reply',?,?,?)",
                       (request_id, canonical(event), created))
            sequence = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        self._notify_events()
        return 201, {"message_id": message_id, "request_id": request_id, "status": "queued_for_device",
                     "event_cursor": sequence, "duplicate": False}

    def _stage_evidence(self, stage, state, evidence):
        if not isinstance(evidence, dict):
            raise InvalidPayload("evidence must be an object")
        if state == "completed" and stage == "raw_stored":
            required = ("note_path", "image_sha256", "writer_revision", "readback_verified")
            if (not exact_keys(evidence, required) or not valid_vault_path(evidence["note_path"])
                    or not evidence["note_path"].startswith("00-inbox/remarkable-")
                    or not isinstance(evidence["image_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", evidence["image_sha256"])
                    or not isinstance(evidence["writer_revision"], str)
                    or not re.fullmatch(r"[0-9a-f]{40,64}", evidence["writer_revision"])
                    or evidence["readback_verified"] is not True):
                raise InvalidPayload("raw storage requires a verified image and committed inbox note")
        elif state == "completed" and stage == "parsed":
            if not exact_keys(evidence, ("summary",)) or not valid_text(evidence["summary"], 4000):
                raise InvalidPayload("completed parsed stage requires a summary")
        elif state == "completed" and stage in ("stored", "organized_recorded"):
            required = ("target_notes", "links", "link_review", "writer_revision", "readback_receipt",
                        "organized_by", "source_capture_id", "organized_at")
            if not exact_keys(evidence, required):
                raise InvalidPayload("completed storage stages require durable write and Hermes provenance evidence")
            if evidence["organized_by"] != "hermes" or not valid_id(evidence["source_capture_id"]):
                raise InvalidPayload("completed storage stages must preserve Hermes and source capture attribution")
            try:
                organized_at = datetime.fromisoformat(evidence["organized_at"].replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                raise InvalidPayload("organized_at must be an ISO timestamp")
            if organized_at.tzinfo is None:
                raise InvalidPayload("organized_at must include a timezone offset")
            if not isinstance(evidence["target_notes"], list) or not 1 <= len(evidence["target_notes"]) <= 20:
                raise InvalidPayload("completed storage stage requires exact target notes")
            target_paths = []
            for note in evidence["target_notes"]:
                if not exact_keys(note, ("title", "path", "operation", "attribution")):
                    raise InvalidPayload("target notes require title, path, operation, and attribution")
                if note["operation"] not in ("create", "append", "section"):
                    raise InvalidPayload("invalid target note operation")
                expected = "new_note_tagged" if note["operation"] == "create" else "existing_change_attributed"
                if note["attribution"] != expected:
                    raise InvalidPayload("target note attribution does not match its operation")
                if not valid_text(note["title"], 500) or not valid_vault_path(note["path"]):
                    raise InvalidPayload("target note title and path must be nonempty")
                target_paths.append(note["path"])
            if len(set(target_paths)) != len(target_paths):
                raise InvalidPayload("target note paths must be unique")
            if not isinstance(evidence["links"], list) or len(evidence["links"]) > 3:
                raise InvalidPayload("links must be a list of at most three exact targets")
            if not all(valid_vault_path(link) for link in evidence["links"]):
                raise InvalidPayload("links must be unique relative Markdown paths")
            if len(set(evidence["links"])) != len(evidence["links"]):
                raise InvalidPayload("links must be unique relative Markdown paths")
            if (stage == "organized_recorded" and not evidence["links"]
                    and any(note["operation"] == "create" and not note["path"].startswith("00-inbox/")
                            for note in evidence["target_notes"])):
                raise InvalidPayload("a new durable note with no meaningful link requires review")
            values = evidence["links"] + [evidence["link_review"], evidence["writer_revision"],
                     evidence["readback_receipt"], evidence["source_capture_id"], evidence["organized_at"]]
            if not all(valid_text(item, 500) for item in values):
                raise InvalidPayload("storage evidence values must be nonempty strings")
        elif state in ("needs_review", "failed"):
            if not exact_keys(evidence, ("reason",)) or not valid_text(evidence["reason"], 2000):
                raise InvalidPayload(f"{state} requires a reason")
        elif state in ("pending", "in_progress") and evidence:
            raise InvalidPayload(f"{state} does not accept evidence")
        return evidence

    def report_stage(self, payload):
        if not exact_keys(payload, ("event_id", "request_id", "actor", "stage", "state", "evidence")):
            return 400, {"error": "invalid stage receipt"}
        event_id, request_id, stage, state = (payload[key] for key in ("event_id", "request_id", "stage", "state"))
        expected_actor = "obsidian" if stage == "raw_stored" else "hermes"
        if (not valid_id(event_id) or not valid_id(request_id) or payload["actor"] != expected_actor
                or stage not in STAGES or state not in STAGE_STATES):
            return 400, {"error": "invalid stage receipt fields"}
        try:
            evidence = self._stage_evidence(stage, state, payload["evidence"])
        except InvalidPayload as error:
            return 400, {"error": str(error)}
        payload_hash = hashlib.sha256(canonical(payload).encode()).hexdigest()
        created = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            thread = db.execute("SELECT destination FROM threads WHERE request_id=?", (request_id,)).fetchone()
            if not thread:
                return 404, {"error": "unknown request thread"}
            if thread["destination"] != "obsidian":
                return 409, {"error": "organizing stages apply only to the Obsidian destination"}
            if evidence.get("source_capture_id", request_id) != request_id:
                return 409, {"error": "source_capture_id must match the request thread"}
            duplicate = db.execute("SELECT payload_hash FROM stage_events WHERE event_id=?", (event_id,)).fetchone()
            if duplicate:
                if duplicate["payload_hash"] != payload_hash:
                    return 409, {"error": "event_id already used for different content"}
                return 200, {"event_id": event_id, "request_id": request_id,
                             "stages": self._latest_stages(db, request_id), "duplicate": True}
            latest = self._latest_stages(db, request_id)
            if latest.get(stage, {}).get("state") == "completed":
                return 409, {"error": "completed stage is terminal"}
            prior = {"stored": "parsed", "organized_recorded": "stored"}.get(stage)
            if state == "completed" and prior and latest.get(prior, {}).get("state") != "completed":
                return 409, {"error": f"{prior} must complete first"}
            db.execute("INSERT INTO stage_events(event_id,request_id,stage,state,evidence,payload_hash,created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (event_id, request_id, stage, state, canonical(evidence), payload_hash, created))
            event = {"kind": "stage_update", "request_id": request_id, "actor": payload["actor"], "stage": stage,
                     "state": state, "evidence": evidence, "created_at": created}
            db.execute("INSERT INTO inbox_events(kind,request_id,payload,created_at) VALUES ('stage_update',?,?,?)",
                       (request_id, canonical(event), created))
            sequence = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            stages = self._latest_stages(db, request_id)
        self._notify_events()
        return 201, {"event_id": event_id, "request_id": request_id, "event_cursor": sequence,
                     "stages": stages, "duplicate": False}

    def _notify_events(self):
        with self.events_changed:
            self.events_changed.notify_all()

    def inbox(self, after, limit=50, wait=0):
        deadline = time.monotonic() + min(max(wait, 0), 25)
        while True:
            with self.events_changed:
                with self.connect() as db:
                    rows = db.execute("SELECT sequence,payload FROM inbox_events WHERE sequence>? "
                                      "ORDER BY sequence LIMIT ?", (after, limit + 1)).fetchall()
                if rows or time.monotonic() >= deadline:
                    break
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    self.events_changed.wait(remaining)
        visible = rows[:limit]
        events = [dict(json.loads(row["payload"]), cursor=row["sequence"]) for row in visible]
        return {"events": events, "cursor": visible[-1]["sequence"] if visible else after,
                "has_more": len(rows) > limit}

    def thread(self, request_id):
        if not valid_id(request_id):
            return 400, {"error": "invalid request_id"}
        with self.connect() as db:
            thread = db.execute("SELECT * FROM threads WHERE request_id=?", (request_id,)).fetchone()
            if not thread:
                return 404, {"error": "unknown request thread"}
            messages = db.execute("SELECT message_id,sender,status,payload,created_at FROM messages "
                                  "WHERE request_id=? ORDER BY sequence", (request_id,)).fetchall()
            return 200, {"request_id": request_id, "destination": thread["destination"],
                         "source": json.loads(thread["source"]),
                         "original": self._client_original(request_id, json.loads(thread["original"])),
                         "messages": [{"message_id": row["message_id"], "sender": row["sender"],
                                       "status": row["status"], "payload": self._client_payload(json.loads(row["payload"])),
                                       "created_at": row["created_at"]} for row in messages],
                         "stages": self._latest_stages(db, request_id)}

    def source_asset(self, request_id):
        if not valid_id(request_id):
            return None
        with self.connect() as db:
            row = db.execute("SELECT original FROM threads WHERE request_id=?", (request_id,)).fetchone()
        if not row:
            return None
        content = json.loads(row["original"])
        image = content.get("image") or content.get("source_image")
        if not image:
            return None
        path = Path(image["stored_path"])
        if not path.is_file():
            return None
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != image["sha256"]:
            return None
        return image["media_type"], data

    def queue_control(self, value):
        if (not exact_keys(value, ("request_id", "operation", "arguments"))
                or not valid_id(value["request_id"])
                or not isinstance(value["operation"], str)
                or value["operation"] not in CONTROL_OPERATIONS
                or not isinstance(value["arguments"], dict)):
            return 400, {"error": "invalid native control command"}
        try:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                                 sort_keys=True, allow_nan=False)
            if len(payload.encode()) > CONTROL_LIMIT:
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            return 400, {"error": "native command exceeds limits or contains invalid values"}
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT payload_hash FROM control_requests WHERE request_id=?",
                             (value["request_id"],)).fetchone()
            if old:
                if old["payload_hash"] != digest:
                    return 409, {"error": "request ID belongs to another native command"}
                return 200, {"accepted": True, "request_id": value["request_id"], "duplicate": True}
            if db.execute("SELECT count(*) FROM control_requests WHERE status!='completed'").fetchone()[0] >= 100:
                return 429, {"error": "native command queue is full"}
            if value["operation"] == "insert_image":
                args = value["arguments"]
                if self.control_asset(args.get("image_sha256"), args.get("image_format")) is None:
                    return 400, {"error": "upload the native image asset before queuing its command"}
            db.execute("INSERT INTO control_requests(request_id,payload_hash,payload,created_at) VALUES(?,?,?,?)",
                       (value["request_id"], digest, payload, now()))
        with self.events_changed:
            self.events_changed.notify_all()
        return 201, {"accepted": True, "request_id": value["request_id"], "duplicate": False}

    def next_control(self, wait=0):
        deadline = time.monotonic() + wait
        with self.events_changed:
            while True:
                with self.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute("SELECT request_id,payload FROM control_requests "
                                     "WHERE status!='completed' ORDER BY sequence LIMIT 1").fetchone()
                    if row:
                        db.execute("UPDATE control_requests SET status='dispatched' WHERE request_id=?",
                                   (row["request_id"],))
                if row:
                    return {"command": json.loads(row["payload"])}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"command": None}
                self.events_changed.wait(remaining)

    def control_status(self, request_id):
        if not valid_id(request_id):
            return 400, {"error": "invalid request ID"}
        with self.connect() as db:
            row = db.execute("SELECT payload,status,receipt,created_at,completed_at FROM control_requests "
                             "WHERE request_id=?", (request_id,)).fetchone()
        if not row:
            return 404, {"error": "unknown native command"}
        return 200, {"request_id": request_id, "operation": json.loads(row["payload"])["operation"],
                     "status": row["status"], "receipt": json.loads(row["receipt"]) if row["receipt"] else None,
                     "created_at": row["created_at"], "completed_at": row["completed_at"]}

    def control_receipt(self, value):
        if (not exact_keys(value, ("request_id", "operation", "status", "result"))
                or not valid_id(value["request_id"])
                or value["status"] not in ("verified", "needs_review", "rejected")
                or not isinstance(value["result"], dict)):
            return 400, {"error": "invalid native receipt"}
        try:
            receipt = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                                 sort_keys=True, allow_nan=False)
            if len(receipt.encode()) > 2 * 1024 * 1024:
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            return 400, {"error": "invalid or oversized native receipt"}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload,status,receipt FROM control_requests WHERE request_id=?",
                             (value["request_id"],)).fetchone()
            if not row:
                return 404, {"error": "unknown native command"}
            if json.loads(row["payload"])["operation"] != value["operation"]:
                return 409, {"error": "native receipt operation mismatch"}
            if row["receipt"]:
                if row["receipt"] != receipt:
                    return 409, {"error": "native receipt conflicts with durable receipt"}
                return 200, {"accepted": True, "request_id": value["request_id"], "duplicate": True}
            if row["status"] != "dispatched":
                return 409, {"error": "native command has not been dispatched"}
            db.execute("UPDATE control_requests SET status='completed',receipt=?,completed_at=? WHERE request_id=?",
                       (receipt, now(), value["request_id"]))
        return 201, {"accepted": True, "request_id": value["request_id"], "duplicate": False}

    def upload_control_asset(self, value):
        try:
            if not exact_keys(value, ("image",)):
                raise InvalidPayload("expected one image")
            image = self._store_image(value["image"], MAX_SOURCE_IMAGE)
        except InvalidPayload as error:
            return 400, {"error": str(error)}
        return 201, {"image_sha256": image["sha256"], "image_format": "png" if image["media_type"] == "image/png" else "jpeg",
                     "size": image["size"]}

    def control_asset(self, digest, image_format):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or image_format not in ("png", "jpeg"):
            return None
        path = self.asset_dir / (digest + (".png" if image_format == "png" else ".jpg"))
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_IMAGE:
            return None
        data = path.read_bytes()
        if not data or hashlib.sha256(data).hexdigest() != digest:
            return None
        return "image/" + image_format, data


def make_server(bridge, device_token, relay_token, port=0):
    token_pattern = r"[A-Za-z0-9_-]{32,128}"
    if not re.fullmatch(token_pattern, device_token) or not re.fullmatch(token_pattern, relay_token):
        raise ValueError("use random URL-safe device and relay tokens of 32–128 characters")
    if hmac.compare_digest(device_token, relay_token):
        raise ValueError("device and relay tokens must differ")

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def authorized(self, expected):
            return hmac.compare_digest(self.headers.get("Authorization", "").encode(),
                                       ("Bearer " + expected).encode())

        def read_json(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                raise ValueError()
            return json.loads(self.rfile.read(length))

        def do_POST(self):
            path = urlsplit(self.path).path
            routes = {
                "/v1/captures": (device_token, bridge.submit),
                "/v1/conversations": (device_token, bridge.conversation),
                "/v1/replies": (device_token, bridge.device_reply),
                "/v1/agent/replies": (relay_token, bridge.agent_reply),
                "/v1/agent/stages": (relay_token, bridge.report_stage),
                "/v1/agent/control/requests": (relay_token, bridge.queue_control),
                "/v1/agent/control/assets": (relay_token, bridge.upload_control_asset),
                "/v1/device/control/receipts": (device_token, bridge.control_receipt),
            }
            if path not in routes:
                self.respond(404, {"error": "not found"})
                return
            expected, action = routes[path]
            if not self.authorized(expected):
                self.respond(401, {"error": "unauthorized"})
                return
            try:
                payload = self.read_json()
            except (ValueError, UnicodeError, RecursionError):
                self.respond(400, {"error": "invalid or oversized JSON body"})
                return
            code, receipt = action(payload)
            self.respond(code, receipt)

        def do_GET(self):
            parsed = urlsplit(self.path)
            expected = relay_token if parsed.path.startswith("/v1/agent/control/requests/") else device_token
            if not self.authorized(expected):
                self.respond(401, {"error": "unauthorized"})
                return
            try:
                if parsed.path.startswith("/v1/agent/control/requests/"):
                    self.respond(*bridge.control_status(parsed.path.removeprefix("/v1/agent/control/requests/")))
                    return
                if parsed.path == "/v1/device/control/next":
                    query = parse_qs(parsed.query)
                    wait = int(query.get("wait", ["0"])[0])
                    if not 0 <= wait <= 25:
                        raise ValueError()
                    self.respond(200, bridge.next_control(wait))
                    return
                if parsed.path.startswith("/v1/device/control/assets/"):
                    match = re.fullmatch(r"([0-9a-f]{64})\.(png|jpeg)", parsed.path.removeprefix("/v1/device/control/assets/"))
                    asset = bridge.control_asset(*match.groups()) if match else None
                    if asset:
                        self.respond_bytes(200, *asset)
                    else:
                        self.respond(404, {"error": "native image asset not found"})
                    return
                if parsed.path in ("/v1/inbox", "/v1/events"):
                    query = parse_qs(parsed.query, strict_parsing=False)
                    after = int(query.get("after", ["0"])[0])
                    limit = int(query.get("limit", ["50"])[0])
                    wait = int(query.get("wait", ["0"])[0]) if parsed.path == "/v1/events" else 0
                    if after < 0 or not 1 <= limit <= 100 or not 0 <= wait <= 25:
                        raise ValueError()
                    self.respond(200, bridge.inbox(after, limit, wait))
                    return
                if parsed.path.startswith("/v1/threads/"):
                    code, body = bridge.thread(parsed.path.removeprefix("/v1/threads/"))
                    self.respond(code, body)
                    return
                if parsed.path.startswith("/v1/sources/"):
                    asset = bridge.source_asset(parsed.path.removeprefix("/v1/sources/"))
                    if not asset:
                        self.respond(404, {"error": "source not found"})
                    else:
                        self.respond_bytes(200, *asset)
                    return
            except (ValueError, OverflowError):
                self.respond(400, {"error": "invalid query"})
                return
            self.respond(404, {"error": "not found"})

        def respond(self, code, receipt):
            body = json.dumps(receipt, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def respond_bytes(self, code, media_type, body):
            self.send_response(code)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--commands", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8898)
    args = parser.parse_args()
    os.umask(0o077)
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    bridge = Bridge(args.state_dir / "bridge.sqlite3", json.loads(args.commands.read_text()))
    make_server(bridge, os.environ.get("REMARKABLE_TOKEN", ""),
                os.environ.get("REMARKABLE_RELAY_TOKEN", ""), args.port).serve_forever()
