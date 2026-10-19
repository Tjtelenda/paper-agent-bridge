import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

from bridge import Bridge, make_server
from muse_handler import session_id, build_message


PNG = b"\x89PNG\r\n\x1a\nMuse-source"


class FakeMuseSocket:
    def __init__(self, root, reply=None):
        self.path = str(root / "muse.sock")
        self.reply = reply or {"ok": True, "status": 200, "response": {"accepted": True}}
        self.request = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.run)

    def run(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(self.path)
            server.listen(1)
            self.ready.set()
            connection, _ = server.accept()
            with connection, connection.makefile("rb") as stream:
                self.request = json.loads(stream.readline())
                connection.sendall(json.dumps(self.reply).encode() + b"\n")

    def start(self):
        self.thread.start()
        if not self.ready.wait(2):
            raise RuntimeError("fake Muse socket did not start")

    def join(self):
        self.thread.join(2)
        if self.thread.is_alive():
            raise RuntimeError("fake Muse socket did not finish")


class MuseAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.handler = Path(__file__).with_name("muse_handler.py")
        self.relay_client = Path(__file__).with_name("relay_client.py")
        self.request_id = "muse_request_00001"
        self.payload = {
            "request_id": self.request_id, "destination": "muse",
            "source": {"notebook_id": "book-1", "page_id": "page-2", "revision": "rev-4",
                       "selection": "page", "captured_by": "remarkable",
                       "captured_at": "2026-10-18T18:00:00-04:00"},
            "content": {"mode": "converted_text", "text": "Ask Muse about this diagram.",
                        "source_image": {"media_type": "image/png",
                                         "data_base64": base64.b64encode(PNG).decode()}},
            "comment": {"mode": "text", "text": "Please keep the source context."},
        }

    def test_source_free_conversation_reaches_muse_and_reply_keeps_session(self):
        fake = FakeMuseSocket(self.root)
        fake.start()
        command = [sys.executable, str(self.handler), "--socket", fake.path, "--timeout", "2"]
        bridge = Bridge(self.root / "bridge.db", {"muse": command})
        payload = {"request_id": self.request_id, "destination": "muse",
                   "content": {"mode": "raw_text", "text": "Start a chat"}}
        self.assertEqual(bridge.conversation(payload)[1]["status"], "handed_off")
        fake.join()
        self.assertEqual(fake.request["session_id"], session_id(self.request_id))
        self.assertIn("Start a chat", fake.request["message"])
        self.assertNotIn('"source"', fake.request["message"])
        envelope = dict(payload, type="device_reply", message_id="device_reply_00001")
        self.assertIn("Start a chat", build_message(envelope))
        self.assertNotIn('"source"', build_message(envelope))
        with self.assertRaises(ValueError):
            build_message(dict(envelope, type="capture"))
        with self.assertRaises(ValueError):
            build_message(dict(envelope, type="conversation", source=self.payload["source"]))

    def test_bridge_hook_uses_stable_side_chat_and_compact_asset_reference(self):
        fake = FakeMuseSocket(self.root)
        fake.start()
        command = [sys.executable, str(self.handler), "--socket", fake.path, "--timeout", "2"]
        bridge = Bridge(self.root / "bridge.db", {"muse": command})
        receipt = bridge.submit(self.payload)[1]
        fake.join()
        self.assertEqual(receipt["status"], "handed_off")
        self.assertEqual(fake.request["session_id"], session_id(self.request_id))
        self.assertIn("Ask Muse about this diagram.", fake.request["message"])
        self.assertIn("source_asset", fake.request["message"])
        self.assertIn("There is no on-device", fake.request["message"])
        self.assertIn("established normal Muse channel", fake.request["message"])
        self.assertNotIn("relay_client.py reply", fake.request["message"])
        self.assertNotIn("data_base64", fake.request["message"])
        self.assertLess(len(json.dumps(fake.request).encode()), 64 * 1024)

    def test_negative_muse_ack_is_uncertain_and_not_replayed(self):
        fake = FakeMuseSocket(self.root, {"ok": False, "error": "not connected"})
        fake.start()
        command = [sys.executable, str(self.handler), "--socket", fake.path, "--timeout", "2"]
        bridge = Bridge(self.root / "bridge.db", {"muse": command})
        first = bridge.submit(self.payload)[1]
        fake.join()
        self.assertEqual(first["status"], "dispatch_uncertain")
        self.assertTrue(bridge.submit(self.payload)[1]["duplicate"])

    def test_relay_client_posts_idempotent_muse_reply_without_exposing_token(self):
        accept = [sys.executable, "-c", "import sys; sys.stdin.read()"]
        bridge = Bridge(self.root / "bridge.db", {"muse": accept})
        bridge.submit(self.payload)
        device_token = "device_" + "a" * 40
        relay_token = "relay_" + "b" * 40
        server = make_server(bridge, device_token, relay_token)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        config = self.root / "relay-config"
        config.mkdir()
        (config / "url").write_text(f"http://127.0.0.1:{server.server_port}")
        (config / "token").write_text(relay_token)
        (config / "token").chmod(0o600)
        answer = self.root / "answer.txt"
        answer.write_text("Muse response returned to the tablet.")
        env = dict(os.environ, REMARKABLE_LOCAL_TEST="1")
        command = [sys.executable, str(self.relay_client), "--config-dir", str(config), "reply",
                   "--request-id", self.request_id, "--in-reply-to", self.request_id,
                   "--actor", "muse", str(answer)]
        first = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        second = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        self.assertFalse(json.loads(first.stdout)["duplicate"])
        self.assertTrue(json.loads(second.stdout)["duplicate"])
        reply = next(event for event in bridge.inbox(0)["events"] if event["kind"] == "agent_reply")
        self.assertEqual((reply["message"]["actor"], reply["message"]["text"]),
                         ("muse", "Muse response returned to the tablet."))
        self.assertNotIn(relay_token, first.stdout + first.stderr + second.stdout + second.stderr)


if __name__ == "__main__":
    unittest.main()
