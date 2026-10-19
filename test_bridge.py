import base64
import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge import Bridge, make_server


PNG = b"\x89PNG\r\n\x1a\nsource-pixels"
INK = b"\x89PNG\r\n\x1a\ncomment-ink"


def image(data):
    return {"media_type": "image/png", "data_base64": base64.b64encode(data).decode()}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.deliveries = self.root / "deliveries"
        command = [sys.executable, "-c",
                   "import sys; from pathlib import Path; "
                   "p=Path(sys.argv[1]); "
                   "f=p.open('a'); f.write(sys.stdin.read()+'\\n'); f.close()",
                   str(self.deliveries)]
        self.commands = {name: command for name in ("muse", "obsidian", "hermes")}
        self.bridge = Bridge(self.root / "bridge.db", self.commands)
        self.request_id = "fixture_request_0001"
        self.source = {
            "notebook_id": "notebook-1", "notebook_title": "Research",
            "page_id": "page-7", "page_label": "7", "revision": "rev-3",
            "selection": "crop", "crop": {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.5},
            "captured_by": "remarkable", "captured_at": "2026-10-18T15:00:00-04:00",
        }
        self.payload = {
            "request_id": self.request_id, "destination": "obsidian", "source": self.source,
            "content": {"mode": "converted_text", "text": "Preserve $(touch /not-executed).",
                        "source_image": image(PNG)},
            "comment": {"mode": "ink", "image": image(INK)},
        }
        self.device_token = "device_" + "a" * 40
        self.agent_token = "agent_" + "b" * 40

    def deliveries_json(self):
        return [json.loads(line) for line in self.deliveries.read_text().splitlines()]

    def conversation(self, destination="hermes"):
        return {"request_id": self.request_id, "destination": destination,
                "content": {"mode": "raw_text", "text": "Can we discuss an idea?"}}

    def test_conversation_is_source_free_idempotent_and_supports_replies(self):
        payload = self.conversation()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.bridge.conversation, [payload] * 4))
        self.assertEqual(sum(result[0] == 201 for result in results), 1)
        delivered = self.deliveries_json()
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["type"], "conversation")
        self.assertNotIn("source", delivered[0])
        self.assertEqual(self.bridge.conversation(dict(payload, content={"mode": "raw_text", "text": "Changed"}))[0], 409)
        self.assertEqual(self.bridge.submit(dict(self.payload, destination="hermes"))[0], 409)
        reply = {"message_id": "agent_response_0001", "request_id": self.request_id,
                 "actor": "hermes", "text": "Yes, tell me more."}
        self.assertEqual(self.bridge.agent_reply(reply)[0], 201)
        self.assertEqual(self.bridge.device_reply({
            "message_id": "device_reply_00001", "request_id": self.request_id,
            "in_reply_to": reply["message_id"], "content": {"mode": "raw_text", "text": "Here is more."},
            "comment": {"mode": "ink", "image": image(INK)}})[0], 201)
        self.assertNotIn("source", self.deliveries_json()[1])
        code, thread = self.bridge.thread(self.request_id)
        self.assertEqual(code, 200)
        self.assertIsNone(thread["source"])
        self.assertEqual(thread["original"], payload["content"])
        self.assertEqual(len(thread["messages"]), 3)
        self.assertNotIn("source_preserved", thread["stages"])
        self.assertIsNone(self.bridge.source_asset(self.request_id))
        events = self.bridge.inbox(0)["events"]
        self.assertEqual(events[0]["kind"], "conversation_accepted")
        self.assertEqual(events[0]["destination"], "hermes")
        self.assertIsNone(events[0]["source"])
        self.assertIsNone(next(event for event in events if event["kind"] == "agent_reply")["source"])
        restarted = Bridge(self.bridge.database, self.commands)
        self.assertEqual(restarted.inbox(0), self.bridge.inbox(0))
        with patch("bridge.subprocess.run") as run:
            self.assertTrue(restarted.conversation(payload)[1]["duplicate"])
            run.assert_not_called()

    def test_conversation_rejects_invalid_payload_and_unconfigured_destination(self):
        payload = self.conversation()
        for invalid in (dict(payload, destination="obsidian"), dict(payload, destination=[]),
                        dict(payload, source=None), dict(payload, request_id="short"),
                        dict(payload, content={"mode": "raw_image", "image": image(PNG)}),
                        dict(payload, content={"mode": "raw_text", "text": " "}),
                        dict(payload, content={"mode": "raw_text", "text": "x" * 16001}),
                        dict(payload, content={"mode": "raw_text", "text": "\ud800"})):
            self.assertEqual(self.bridge.conversation(invalid)[0], 400)
        self.assertEqual(self.bridge.conversation(self.conversation("codex"))[0], 503)
        self.assertFalse(self.deliveries.exists())
        self.assertEqual(list((self.root / "assets").iterdir()), [])
        self.assertEqual(self.bridge.inbox(0)["events"], [])
        self.assertEqual(self.bridge.submit(payload)[0], 400)

    def test_codex_chat_history_is_bounded_and_excludes_current_message(self):
        self.bridge.commands["codex"] = self.commands["hermes"]
        payload = self.conversation("codex")
        self.assertEqual(self.bridge.conversation(payload)[0], 201)
        self.bridge.agent_reply({"message_id": "agent_response_0001", "request_id": self.request_id,
                                 "actor": "codex", "text": "Prior answer."})
        reply = {"message_id": "device_reply_00001", "request_id": self.request_id,
                 "content": {"mode": "raw_text", "text": "Current question"}}
        self.assertEqual(self.bridge.device_reply(reply)[0], 201)
        history = self.deliveries_json()[-1]["history"]
        self.assertEqual([message["text"] for message in history["messages"]],
                         [payload["content"]["text"], "Prior answer."])
        self.assertFalse(history["truncated"])
        for index in range(14):
            self.bridge.agent_reply({"message_id": f"agent_history_{index:04}", "request_id": self.request_id,
                                     "actor": "codex", "text": "x" * 1100})
        self.bridge.device_reply(dict(reply, message_id="device_reply_00002"))
        history = self.deliveries_json()[-1]["history"]
        self.assertTrue(history["truncated"])
        self.assertLessEqual(len(history["messages"]), 12)
        self.assertLessEqual(sum(len(message["text"]) for message in history["messages"]), 12000)
        self.assertTrue(all(set(message) == {"message_id", "sender", "text"} for message in history["messages"]))

    def test_conversation_checks_actual_adapter_size_before_acceptance(self):
        self.bridge.commands["codex"] = self.commands["hermes"]
        for destination in ("muse", "hermes", "codex"):
            payload = dict(self.conversation(destination), content={"mode": "raw_text", "text": "\x01" * 11000})
            self.assertEqual(self.bridge.conversation(payload)[0], 413)
            self.assertEqual(self.bridge.inbox(0)["events"], [])
            self.assertFalse(self.deliveries.exists())
        payload = self.conversation("muse")
        self.assertEqual(self.bridge.conversation(payload)[0], 201)
        before = self.bridge.inbox(0)
        reply = {"request_id": self.request_id, "message_id": "oversized_reply_001",
                 "content": {"mode": "raw_text", "text": "\x01" * 11000}}
        self.assertEqual(self.bridge.device_reply(reply)[0], 413)
        self.assertEqual(self.bridge.inbox(0), before)
        self.assertEqual(len(self.bridge.thread(self.request_id)[1]["messages"]), 1)
        self.assertEqual(len(self.deliveries_json()), 1)
        reply["content"]["text"] = "😀" * 16000
        reply["comment"] = {"mode": "text", "text": '"' * 4000}
        self.assertEqual(self.bridge.device_reply(reply)[0], 413)
        self.assertEqual(self.bridge.inbox(0), before)
        reply.pop("comment")
        reply["content"]["text"] = "😀" * 1000
        self.assertEqual(self.bridge.device_reply(reply)[0], 201)

    def test_codex_history_trims_to_actual_serialized_prompt_budget(self):
        self.bridge.commands["codex"] = self.commands["hermes"]
        self.bridge.conversation(self.conversation("codex"))
        for index in range(4):
            self.bridge.agent_reply({"message_id": f"escaped_answer_{index:03}", "request_id": self.request_id,
                                     "actor": "codex", "text": "\x01" * 3000})
        reply = {"request_id": self.request_id, "message_id": "codex_followup_001",
                 "content": {"mode": "raw_text", "text": "😀" * 12000}}
        self.assertEqual(self.bridge.device_reply(reply)[0], 201)
        envelope = self.deliveries_json()[-1]
        self.assertTrue(envelope["history"]["truncated"])
        self.assertLess(len(envelope["history"]["messages"]), 4)
        from agent_handler import build_prompt
        self.assertLessEqual(len(build_prompt(envelope, None)[0].encode()), 64 * 1024)

    def test_conversation_http_auth_and_missing_source(self):
        base = self.start_server()
        payload = self.conversation()
        self.assertEqual(self.request(base, "/v1/conversations", self.agent_token, payload)[0], 401)
        self.assertEqual(self.request(base, "/v1/conversations", self.device_token, payload)[0], 201)
        self.assertEqual(self.request(base, f"/v1/sources/{self.request_id}", self.device_token)[0], 404)

    def test_conversation_uncertain_handoff_is_never_replayed(self):
        with patch("bridge.subprocess.run", side_effect=subprocess.TimeoutExpired("mock", 20)) as run:
            self.assertEqual(self.bridge.conversation(self.conversation())[1]["status"], "dispatch_uncertain")
            self.assertTrue(self.bridge.conversation(self.conversation())[1]["duplicate"])
            self.assertEqual(run.call_count, 1)

    def test_capture_preserves_source_and_ink_and_duplicate_dispatches_once(self):
        code, first = self.bridge.submit(self.payload)
        self.assertEqual((code, first["status"]), (201, "handed_off"))
        self.assertEqual(first["stages"]["parsed"]["state"], "pending")
        second = self.bridge.submit(self.payload)
        self.assertEqual((second[0], second[1]["duplicate"]), (200, True))
        delivery = self.deliveries_json()[0]
        self.assertEqual(delivery["source"], self.source)
        self.assertEqual(Path(delivery["content"]["source_image"]["stored_path"]).read_bytes(), PNG)
        self.assertEqual(Path(delivery["comment"]["image"]["stored_path"]).read_bytes(), INK)
        self.assertNotIn("data_base64", json.dumps(delivery))
        code, thread = self.bridge.thread(self.request_id)
        self.assertEqual(code, 200)
        self.assertNotIn("stored_path", json.dumps(thread))

    def test_raw_text_typed_comment_and_hermes_destination(self):
        payload = dict(self.payload, request_id="fixture_request_0002", destination="hermes",
                       source=dict(self.source, selection="page"),
                       content={"mode": "raw_text", "text": "Already plain text"},
                       comment={"mode": "text", "text": "Please answer directly."})
        payload["source"].pop("crop")
        self.assertEqual(self.bridge.submit(payload)[1]["status"], "handed_off")
        delivered = self.deliveries_json()[0]
        self.assertEqual(delivered["content"], payload["content"])
        self.assertEqual(delivered["comment"], payload["comment"])

    def test_invalid_boundary_input_never_dispatches(self):
        cases = [
            dict(self.payload, command="sh"),
            dict(self.payload, content={"mode": "raw_text", "text": " "}),
            dict(self.payload, content={"mode": "raw_image", "image": image(b"not png")}),
            dict(self.payload, source=dict(self.source, captured_by="muse")),
            dict(self.payload, source=dict(self.source, captured_at="2026-10-18T15:00:00")),
            dict(self.payload, source=dict(self.source, notebook_title="\ud800")),
            dict(self.payload, source=dict(self.source, crop={"x": .9, "y": 0, "width": .2, "height": .5})),
            dict(self.payload, comment={"mode": "text", "text": "x" * 4001}),
        ]
        for payload in cases:
            self.assertEqual(self.bridge.submit(payload)[0], 400)
        self.assertFalse(self.deliveries.exists())

    def test_unconfigured_destination_accepts_nothing(self):
        payload = dict(self.payload, destination="codex")
        self.assertEqual(self.bridge.submit(payload)[0], 503)
        self.assertEqual(list((self.root / "assets").iterdir()), [])
        with sqlite3.connect(self.bridge.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM threads").fetchone()[0], 0)

    def test_concurrent_duplicate_dispatches_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.bridge.submit, [self.payload] * 4))
        self.assertEqual(sum(not result[1]["duplicate"] for result in results), 1)
        self.assertEqual(len(self.deliveries_json()), 1)

    def test_failed_dispatch_is_durable_and_not_replayed(self):
        failing = [sys.executable, "-c", "print('private diagnostic'); exit(1)"]
        self.bridge.commands["obsidian"] = failing
        first = self.bridge.submit(self.payload)
        self.assertEqual(first[1]["status"], "dispatch_uncertain")
        self.assertEqual(first[1]["stages"]["relay_handoff"]["state"], "needs_review")
        with patch("bridge.subprocess.run") as run:
            self.assertTrue(self.bridge.submit(self.payload)[1]["duplicate"])
            run.assert_not_called()
        restarted = Bridge(self.bridge.database, self.bridge.commands)
        self.assertTrue(restarted.submit(self.payload)[1]["duplicate"])

    def test_timeout_is_uncertain_without_handler_output(self):
        with patch("bridge.subprocess.run", side_effect=subprocess.TimeoutExpired("private-diagnostic", 20)) as run:
            result = self.bridge.submit(self.payload)
        self.assertEqual(result[1]["status"], "dispatch_uncertain")
        self.assertNotIn("private-diagnostic", json.dumps(result))
        self.assertEqual(run.call_count, 1)

    def test_device_reply_keeps_thread_and_parent(self):
        self.bridge.submit(self.payload)
        reply = {"message_id": "device_reply_00001", "request_id": self.request_id,
                 "in_reply_to": self.request_id,
                 "content": {"mode": "raw_text", "text": "Follow-up answer"}}
        result = self.bridge.device_reply(reply)
        self.assertEqual((result[0], result[1]["status"]), (201, "handed_off"))
        self.assertEqual(self.deliveries_json()[1]["in_reply_to"], self.request_id)
        self.assertTrue(self.bridge.device_reply(reply)[1]["duplicate"])
        invalid = dict(reply, message_id="device_reply_00002", in_reply_to="missing_parent_000")
        self.assertEqual(self.bridge.device_reply(invalid)[0], 400)

    def test_agent_reply_enters_durable_inbox_with_original_and_source(self):
        self.bridge.submit(self.payload)
        reply = {"message_id": "agent_response_0001", "request_id": self.request_id,
                 "in_reply_to": self.request_id, "actor": "hermes",
                 "text": "I organized the requested material."}
        result = self.bridge.agent_reply(reply)
        self.assertEqual(result[1]["status"], "queued_for_device")
        inbox = self.bridge.inbox(0)
        self.assertEqual([item["kind"] for item in inbox["events"][:2]],
                         ["capture_accepted", "handoff_update"])
        event = next(item for item in inbox["events"] if item["kind"] == "agent_reply")
        self.assertEqual(event["message"]["in_reply_to"], self.request_id)
        self.assertEqual(event["source"]["revision"], "rev-3")
        self.assertEqual(event["original"]["sha256"], __import__("hashlib").sha256(PNG).hexdigest())
        restarted = Bridge(self.bridge.database, self.commands)
        self.assertEqual(restarted.inbox(0), inbox)

    def stage_evidence(self):
        return {
            "target_notes": [{"title": "Research Note", "path": "40-knowledge/Research Note.md",
                              "operation": "create", "attribution": "new_note_tagged"}],
            "links": ["40-knowledge/Existing Context.md"],
            "link_review": "One exact retrieved candidate explains the relationship.",
            "writer_revision": "commit:abc123", "readback_receipt": "sha256:def456",
            "organized_by": "hermes", "source_capture_id": self.request_id,
            "organized_at": "2026-10-18T20:00:00Z",
        }

    def test_obsidian_stages_require_order_and_durable_evidence(self):
        self.bridge.submit(self.payload)
        stored = {"event_id": "stored_event_0001", "request_id": self.request_id, "actor": "hermes",
                  "stage": "stored", "state": "completed", "evidence": self.stage_evidence()}
        self.assertEqual(self.bridge.report_stage(stored)[0], 409)
        parsed = {"event_id": "parsed_event_0001", "request_id": self.request_id, "actor": "hermes",
                  "stage": "parsed", "state": "completed", "evidence": {"summary": "Decision extracted."}}
        self.assertEqual(self.bridge.report_stage(parsed)[0], 201)
        self.assertEqual(self.bridge.report_stage(stored)[0], 201)
        orphan = dict(stored, event_id="organized_orphan_1", stage="organized_recorded",
                      evidence=dict(stored["evidence"], links=[],
                                    link_review="No meaningful existing link was found."))
        self.assertEqual(self.bridge.report_stage(orphan)[0], 400)
        final = dict(stored, event_id="organized_event_01", stage="organized_recorded")
        result = self.bridge.report_stage(final)
        self.assertEqual(result[1]["stages"]["organized_recorded"]["state"], "completed")
        self.assertTrue(self.bridge.report_stage(final)[1]["duplicate"])
        changed = dict(final, evidence=dict(final["evidence"], writer_revision="changed"))
        self.assertEqual(self.bridge.report_stage(changed)[0], 409)

    def test_stages_reject_false_final_or_wrong_provenance(self):
        self.bridge.submit(self.payload)
        bad = self.stage_evidence()
        bad["organized_by"] = "muse"
        payload = {"event_id": "stored_event_0001", "request_id": self.request_id, "actor": "hermes",
                   "stage": "stored", "state": "completed", "evidence": bad}
        self.assertEqual(self.bridge.report_stage(payload)[0], 400)
        bad_path = self.stage_evidence()
        bad_path["target_notes"][0]["path"] = "../outside.md"
        path_payload = dict(payload, event_id="bad_path_event_01", evidence=bad_path)
        self.assertEqual(self.bridge.report_stage(path_payload)[0], 400)
        for index, invalid_links in enumerate(([{}], [[]])):
            bad_links = self.stage_evidence()
            bad_links["links"] = invalid_links
            link_payload = dict(payload, event_id=f"bad_links_event_{index}", evidence=bad_links)
            self.assertEqual(self.bridge.report_stage(link_payload)[0], 400)
        needs_review = {"event_id": "review_event_0001", "request_id": self.request_id, "actor": "hermes",
                        "stage": "parsed", "state": "needs_review",
                        "evidence": {"reason": "Handwriting is ambiguous."}}
        self.assertEqual(self.bridge.report_stage(needs_review)[1]["stages"]["parsed"]["state"], "needs_review")

    def start_server(self):
        server = make_server(self.bridge, self.device_token, self.agent_token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def request(self, base, path, token, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(base + path, data=data, headers={"Authorization": "Bearer " + token,
                                                           "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=8) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def test_http_separates_device_and_agent_authority_and_serves_source(self):
        base = self.start_server()
        self.assertEqual(self.request(base, "/v1/captures", self.agent_token, self.payload)[0], 401)
        self.assertEqual(self.request(base, "/v1/captures", self.device_token, self.payload)[0], 201)
        reply = {"message_id": "agent_response_0001", "request_id": self.request_id,
                 "actor": "hermes", "text": "A response."}
        self.assertEqual(self.request(base, "/v1/agent/replies", self.device_token, reply)[0], 401)
        self.assertEqual(self.request(base, "/v1/agent/replies", self.agent_token, reply)[0], 201)
        status, headers, source = self.request(base, f"/v1/sources/{self.request_id}", self.device_token)
        self.assertEqual((status, headers.get_content_type(), source), (200, "image/png", PNG))
        inbox = json.loads(self.request(base, "/v1/inbox?after=0", self.device_token)[2])
        reply_event = next(item for item in inbox["events"] if item["kind"] == "agent_reply")
        self.assertEqual(reply_event["message"]["text"], "A response.")

    def test_bounded_event_wait_wakes_for_agent_reply(self):
        base = self.start_server()
        self.bridge.submit(self.payload)
        cursor = self.bridge.inbox(0)["cursor"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(self.request, base, f"/v1/events?after={cursor}&wait=3", self.device_token)
            time.sleep(0.1)
            self.bridge.agent_reply({"message_id": "agent_response_0001", "request_id": self.request_id,
                                     "actor": "hermes", "text": "Wake the device event wait."})
            status, _, body = waiting.result(timeout=4)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["events"][0]["kind"], "agent_reply")

    def test_event_committed_before_wait_is_not_lost(self):
        self.bridge.submit(self.payload)
        cursor = self.bridge.inbox(0)["cursor"]
        entered = threading.Event()
        posted = threading.Event()

        class GateCondition:
            def __init__(self):
                self.real = threading.Condition()
                self.first = True

            def __enter__(self):
                if self.first:
                    self.first = False
                    entered.set()
                    posted.wait(2)
                return self.real.__enter__()

            def __exit__(self, *args):
                return self.real.__exit__(*args)

            def wait(self, timeout=None):
                return self.real.wait(timeout)

            def notify_all(self):
                return self.real.notify_all()

        self.bridge.events_changed = GateCondition()

        def post_between_query_and_wait():
            entered.wait(2)
            self.bridge.agent_reply({"message_id": "agent_response_0001", "request_id": self.request_id,
                                     "actor": "hermes", "text": "Committed at the wait boundary."})
            posted.set()

        worker = threading.Thread(target=post_between_query_and_wait)
        worker.start()
        started = time.monotonic()
        result = self.bridge.inbox(cursor, wait=0.8)
        elapsed = time.monotonic() - started
        worker.join(2)
        self.assertEqual(result["events"][0]["kind"], "agent_reply")
        self.assertLess(elapsed, 0.5)

    def test_sender_posts_capture_and_reads_inbox(self):
        base = self.start_server()
        config = self.root / "device"
        config.mkdir()
        (config / "url").write_text(base)
        (config / "token").write_text(self.device_token)
        env = dict(os.environ, REMARKABLE_CONFIG_DIR=str(config), REMARKABLE_LOCAL_TEST="1")
        client = str(Path(__file__).with_name("send.sh"))
        large_png = b"\x89PNG\r\n\x1a\n" + b"x" * 200000
        payload = dict(self.payload, content={"mode": "raw_image", "image": image(large_png)})
        payload_file = self.root / "capture.json"
        payload_file.write_text(json.dumps(payload))
        first = subprocess.run(["sh", client, "capture", str(payload_file)], env=env,
                               capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(first.stdout)["status"], "handed_off")
        self.bridge.agent_reply({"message_id": "agent_response_0001", "request_id": self.request_id,
                                 "actor": "hermes", "text": "Visible in manual refresh."})
        inbox = subprocess.run(["sh", client, "inbox", "0"], env=env,
                               capture_output=True, text=True, check=True)
        events = json.loads(inbox.stdout)["events"]
        self.assertIn("agent_reply", [event["kind"] for event in events])
        self.assertEqual(self.bridge.source_asset(self.request_id)[1], large_png)
        chat_file = self.root / "conversation.json"
        chat_file.write_text(json.dumps(dict(self.conversation(), request_id="shell_chat_000001")))
        chat = subprocess.run(["sh", client, "conversation", str(chat_file)], env=env,
                              capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(chat.stdout)["status"], "handed_off")
        self.assertIsNone(self.bridge.thread("shell_chat_000001")[1]["source"])
        self.assertNotIn(self.device_token, chat.stdout + chat.stderr)
        self.assertNotIn(self.device_token, first.stdout + first.stderr + inbox.stdout + inbox.stderr)
        env.pop("REMARKABLE_LOCAL_TEST")
        self.assertNotEqual(subprocess.run(["sh", client, "inbox", "0"], env=env,
                                           capture_output=True).returncode, 0)

    def test_http_body_limit(self):
        base = self.start_server()
        request = Request(base + "/v1/captures", data=b"x",
                          headers={"Authorization": "Bearer " + self.device_token,
                                   "Content-Length": str(8 * 1024 * 1024 + 1)})
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
