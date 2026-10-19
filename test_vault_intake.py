import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from vault_intake import read_asset, receipt, store
import test_bridge


class RawIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.pixels = b"\x89PNG\r\n\x1a\noriginal"
        self.path = self.assets / "image.png"
        self.path.write_bytes(self.pixels)
        self.asset = {"stored_path": str(self.path), "sha256": hashlib.sha256(self.pixels).hexdigest(),
                      "size": len(self.pixels), "media_type": "image/png"}
        self.envelope = {"type": "capture", "request_id": "fixture_request_0001",
                         "message_id": "fixture_message_0001", "destination": "obsidian",
                         "source": {"notebook_id": "notebook-1", "page_id": "page-1", "revision": "rev-1",
                                    "selection": "page", "captured_by": "remarkable",
                                    "captured_at": "2026-10-19T10:00:00Z", "notebook_title": "Example"},
                         "content": {"mode": "raw_image", "image": self.asset},
                         "comment": {"mode": "text", "text": "Keep this"}}
        self.result = {"status": "raw_stored", "note_path": "00-inbox/remarkable-fixture_request_0001.md",
                       "image_sha256": self.asset["sha256"], "git_commit": "a" * 40,
                       "readback_verified": True}
        self.writer = SimpleNamespace(
            LOCKFILE=self.root / "writer.lock", RAW_CAPTURE_PROVENANCE_KEYS=(
                "notebook_id", "page_id", "title", "label", "selection", "crop", "revision", "captured_at", "captured_by"),
            live_write_raw_image_capture=Mock(return_value=self.result))

    def test_original_and_comment_pass_to_single_sanctioned_writer(self):
        self.assertEqual(store(self.envelope, self.writer, self.assets), self.result)
        arguments = self.writer.live_write_raw_image_capture.call_args.kwargs
        self.assertEqual(arguments["image"]["bytes"], self.pixels)
        self.assertEqual(arguments["provenance"]["title"], "Example")
        self.assertEqual(arguments["comment_text"], "Keep this")

    def test_asset_digest_path_and_symlinks_fail_closed(self):
        self.path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            store(self.envelope, self.writer, self.assets)
        self.path.write_bytes(self.pixels)
        with self.assertRaises(ValueError):
            read_asset(self.asset, self.root, 4096)
        linked = self.assets / "linked.png"
        linked.symlink_to(self.path)
        with self.assertRaises(OSError):
            read_asset(dict(self.asset, stored_path=str(linked)), self.assets, 4096)
        self.writer.live_write_raw_image_capture.assert_not_called()

    def test_not_conversation_or_wrong_destination(self):
        for key, value in (("type", "device_reply"), ("destination", "hermes")):
            with self.assertRaises(ValueError):
                store(dict(self.envelope, **{key: value}), self.writer, self.assets)
        self.writer.live_write_raw_image_capture.assert_not_called()

    def test_raw_receipt_is_separate_and_requires_readback(self):
        event = receipt(self.envelope, self.result)
        self.assertEqual(event["stage"], "raw_stored")
        fixture = test_bridge.BridgeTests("test_conversation_is_source_free_idempotent_and_supports_replies")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.bridge.submit(fixture.payload)
        self.assertEqual(fixture.bridge.report_stage(event)[0], 201)
        self.assertEqual(fixture.bridge.report_stage(event)[0], 200)
        self.assertEqual(fixture.bridge.report_stage(dict(event, actor="hermes"))[0], 400)
        self.assertNotEqual(fixture.bridge.thread(fixture.request_id)[1]["stages"]["parsed"]["state"], "completed")
        with self.assertRaises(ValueError):
            receipt(self.envelope, dict(self.result, readback_verified=False))


if __name__ == "__main__":
    unittest.main()
