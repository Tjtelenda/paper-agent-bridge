import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_handler import HandlerError, RUNTIME_MAX, WORKER_TIMEOUT, accept, build_prompt, run_job, validate_envelope


class Result:
    def __init__(self, returncode=0):
        self.returncode = returncode


class Response:
    def __init__(self, status, payload):
        self.status = status
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, limit=-1):
        return self.payload


class AgentHandlerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.config.mkdir(mode=0o700)
        hermes = self.config / "hermes"
        hermes.mkdir(mode=0o700)
        (hermes / "url").write_text("http://127.0.0.1:8642")
        (hermes / "token").write_text("h" * 40)
        (hermes / "token").chmod(0o600)
        (hermes / "routing").write_text("inherited")
        codex = self.config / "codex"
        codex.mkdir(mode=0o700)
        binary = self.root / "codex"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o700)
        workspace = self.root / "workspace"
        workspace.mkdir()
        (codex / "bin").write_text(str(binary))
        (codex / "workspace").write_text(str(workspace))
        self.base = {
            "type": "capture", "request_id": "request_00000001", "message_id": "request_00000001",
            "destination": "hermes",
            "source": {"notebook_id": "book", "page_id": "page", "revision": "r1",
                       "selection": "page", "captured_by": "remarkable",
                       "captured_at": "2026-10-18T18:00:00-04:00"},
            "content": {"mode": "raw_text", "text": "Please explain this note."},
        }

    def accepted_job(self, envelope):
        calls = []

        def launcher(command, **kwargs):
            calls.append((command, kwargs))
            return Result()

        receipt = accept(envelope, self.state, self.config, launcher=launcher)
        return self.state / "jobs" / envelope["message_id"], receipt, calls

    def test_source_free_conversation_and_reply_validate_without_relaxing_capture(self):
        envelope = dict(self.base, type="conversation")
        envelope.pop("source")
        self.assertEqual(validate_envelope(envelope), envelope)
        reply = dict(envelope, type="device_reply", message_id="device_reply_00001")
        self.assertEqual(validate_envelope(reply), reply)
        for invalid in (dict(envelope, type="capture"), dict(envelope, destination="obsidian"),
                        dict(envelope, source=self.base["source"]),
                        dict(envelope, content={"mode": "raw_image"})):
            with self.assertRaises(HandlerError):
                validate_envelope(invalid)

    def test_codex_source_free_turn_includes_bounded_history_and_no_image(self):
        envelope = dict(self.base, type="device_reply", destination="codex", history={
            "messages": [{"message_id": "agent_answer_00001", "sender": "relay:codex", "text": "Prior answer"}],
            "truncated": True})
        envelope.pop("source")
        job, _, _ = self.accepted_job(envelope)
        calls = []

        def agent(command, **kwargs):
            calls.append((command, kwargs))
            Path(command[command.index("-o") + 1]).write_text("A follow-up answer")
            return Result()

        self.assertEqual(run_job(job, self.config, agent_runner=agent), 0)
        command, kwargs = calls[0]
        self.assertNotIn("--image", command)
        self.assertIn(b"Prior answer", kwargs["input"])
        self.assertIn(b'"truncated": true', kwargs["input"])
        self.assertNotIn(b'"source"', kwargs["input"])
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")

    def test_hermes_source_free_chat_uses_same_session_for_followup(self):
        envelope = dict(self.base, type="conversation")
        envelope.pop("source")
        sessions = []
        chats = []

        def opener(request, timeout):
            body = json.loads(request.data)
            if request.full_url.endswith("/api/sessions"):
                sessions.append(body["id"])
                return Response(201, {"session": {"id": body["id"]}})
            chats.append(body["message"])
            return Response(200, {"message": {"content": "Chat answer"}})

        for turn in (envelope, dict(envelope, type="device_reply", message_id="device_reply_00001")):
            job, _, _ = self.accepted_job(turn)
            self.assertEqual(run_job(job, self.config, opener=opener), 0)
        self.assertEqual(sessions[0], sessions[1])
        self.assertTrue(all('"source"' not in chat for chat in chats))

    def test_accept_is_durable_supervised_and_idempotent_without_relaunch(self):
        job, receipt, calls = self.accepted_job(self.base)
        self.assertTrue(receipt["accepted"])
        self.assertEqual(len(calls), 1)
        command = calls[0][0]
        self.assertIn(f"--property=RuntimeMaxSec={RUNTIME_MAX}", command)
        self.assertIn("worker", command)
        self.assertEqual(json.loads((job / "status.json").read_text())["state"], "queued")
        self.assertEqual(json.loads((job / "launch.json").read_text())["state"], "accepted")
        duplicate = accept(self.base, self.state, self.config, launcher=lambda *_args, **_kwargs: self.fail())
        self.assertTrue(duplicate["duplicate"])

    def test_uncertain_launch_is_never_relaunched_or_reported_accepted(self):
        def timeout(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("systemd-run", 10)

        with self.assertRaises(HandlerError):
            accept(self.base, self.state, self.config, launcher=timeout)
        with self.assertRaises(HandlerError):
            accept(self.base, self.state, self.config, launcher=lambda *_args, **_kwargs: Result())
        job = self.state / "jobs" / self.base["message_id"]
        self.assertEqual(json.loads((job / "launch.json").read_text())["state"], "uncertain")

    def test_hermes_uses_only_verified_inherited_api_and_does_not_replay(self):
        job, _, _ = self.accepted_job(self.base)
        requests = []

        def opener(request, timeout):
            body = json.loads(request.data)
            requests.append((request.full_url, body, timeout, dict(request.header_items())))
            if request.full_url.endswith("/api/sessions"):
                return Response(201, {"session": {"id": body["id"]}})
            return Response(200, {"session_id": requests[0][1]["id"],
                                  "message": {"role": "assistant", "content": "Hermes answer"}})

        self.assertEqual(run_job(job, self.config, opener=opener), 0)
        self.assertEqual(requests[0][1].keys(), {"id", "source"})
        self.assertEqual(requests[1][1].keys(), {"message", "instructions"})
        self.assertEqual(requests[1][2], WORKER_TIMEOUT)
        chat_headers = {key.lower(): value for key, value in requests[1][3].items()}
        self.assertEqual(chat_headers["x-hermes-session-id"], requests[0][1]["id"])
        self.assertEqual(chat_headers["x-hermes-session-key"], "remarkable:" + self.base["request_id"])
        self.assertNotIn("provider", json.dumps(requests))
        self.assertEqual(json.loads((job / "status.json").read_text())["state"], "completed")
        self.assertEqual((job / "answer.txt").read_text(), "Hermes answer")
        status = json.loads((job / "status.json").read_text())
        self.assertFalse(status["tablet_reply"])
        self.assertEqual(status["normal_channel_delivery"], "not_verified")
        self.assertIn("There is no on-device chat", requests[1][1]["message"])
        self.assertEqual(run_job(job, self.config, opener=lambda *_args: self.fail()), 0)

    def test_obsidian_fails_closed_before_job_or_agent_dispatch(self):
        envelope = dict(self.base, destination="obsidian")
        with self.assertRaisesRegex(HandlerError, "local-only Hermes route"):
            accept(envelope, self.state, self.config, launcher=lambda *_args, **_kwargs: self.fail())
        self.assertFalse((self.state / "jobs").exists())
        prompt, _ = build_prompt(envelope, self.config)
        self.assertIn("HERMES-OBSIDIAN.md", prompt)
        self.assertIn("sanctioned transactional writer", prompt)

    def test_deferred_hermes_turn_is_preserved_without_relay_or_replay(self):
        envelope = dict(self.base, message_id="request_00000002")
        job, _, _ = self.accepted_job(envelope)

        def opener(request, timeout):
            if request.full_url.endswith("/api/sessions"):
                return Response(201, {"session": {"id": json.loads(request.data)["id"]}})
            return Response(200, {"deferred": True, "recoverable": True,
                                  "message": {"content": "Queued elsewhere"}})

        self.assertEqual(run_job(job, self.config, opener=opener), 1)
        self.assertEqual(json.loads((job / "status.json").read_text())["state"], "agent_uncertain")
        self.assertEqual(run_job(job, self.config, opener=lambda *_args: self.fail()), 1)

    def test_hermes_session_mismatch_stops_before_chat(self):
        envelope = dict(self.base, message_id="request_00000003")
        job, _, _ = self.accepted_job(envelope)
        urls = []

        def opener(request, timeout):
            urls.append(request.full_url)
            return Response(201, {"session": {"id": "different_session"}})

        self.assertEqual(run_job(job, self.config, opener=opener), 1)
        self.assertEqual(len(urls), 1)
        self.assertEqual(json.loads((job / "status.json").read_text())["state"], "agent_uncertain")

    def test_codex_worker_is_read_only_and_passes_verified_source_image(self):
        image = self.root / "source.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nsource")
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        envelope = dict(self.base, destination="codex", content={
            "mode": "converted_text", "text": "Converted words",
            "source_image": {"stored_path": str(image), "sha256": digest,
                             "size": image.stat().st_size, "media_type": "image/png"},
        })
        job, _, _ = self.accepted_job(envelope)
        agent_calls = []

        def agent(command, **kwargs):
            agent_calls.append((command, kwargs))
            Path(command[command.index("-o") + 1]).write_text("Codex answer")
            return Result()

        self.assertEqual(run_job(job, self.config, agent_runner=agent), 0)
        command, kwargs = agent_calls[0]
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[command.index("--image") + 1], str(image))
        self.assertEqual(command[-1], "-")
        self.assertIn(b"Converted words", kwargs["input"])
        self.assertNotIn("dangerously", " ".join(command))
        self.assertFalse(json.loads((job / "status.json").read_text())["tablet_reply"])


if __name__ == "__main__":
    unittest.main()
