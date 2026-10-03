import io
import json
import logging
import sqlite3
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_worker
import operational_logging
import slack_bridge
from operational_logging import _safe_console_write, emit_lifecycle


class NonTtyStream(io.StringIO):
    def isatty(self):
        return False


class AsciiStream(io.StringIO):
    encoding = "ascii"

    def write(self, value):
        value.encode("ascii")
        return super().write(value)


class ConsoleObservabilityTests(unittest.TestCase):
    def setUp(self):
        agent_worker._execution_context.lifecycle_started = None
        agent_worker._execution_context.lifecycle_ended = None

    @staticmethod
    def job(job_id="OBS-JOB"):
        return SimpleNamespace(
            job_id=job_id, actor="codex", mode="execute", workspace="sandbox",
            callback_type="chatgpt_browser", callback_url="https://example.invalid",
            protocol_version="2", prompt_sha256="0" * 64,
        )

    def test_threaded_claim_marks_running_before_immediate_start_and_one_end(self):
        job = self.job()
        output = NonTtyStream()
        order = []
        row = {"status": "DONE", "exit_code": 0}

        def mark_running(*args):
            order.append("RUNNING")

        def emit(message):
            order.append("START" if "JOB START" in message else "END")
            _safe_console_write(message + "\n", output)

        with patch.object(agent_worker.state_store, "mark_running", side_effect=mark_running), \
             patch.object(agent_worker.state_store, "get_job", return_value=row), \
             patch.object(agent_worker, "execute_job"), \
             patch.object(agent_worker, "dispatch_next_queued"), \
             patch.object(agent_worker, "emit_lifecycle", side_effect=emit):
            thread = threading.Thread(target=agent_worker.execute_claimed_job, args=(job, Mock()))
            thread.start()
            thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(order, ["RUNNING", "START", "END"])
        self.assertEqual(output.getvalue().count("JOB START"), 1)
        self.assertEqual(output.getvalue().count("JOB END"), 1)

    def test_failed_setup_and_callback_failure_still_end_once(self):
        job = self.job("OBS-FAIL")
        output = NonTtyStream()
        row = {"status": "FAILED", "exit_code": None}
        with patch.object(agent_worker.state_store, "mark_running"), \
             patch.object(agent_worker.state_store, "mark_completed"), \
             patch.object(agent_worker.state_store, "get_job", return_value=row), \
             patch.object(agent_worker, "execute_job", side_effect=RuntimeError("setup")), \
             patch.object(agent_worker, "build_result", return_value={"status": "FAILED"}), \
             patch.object(agent_worker, "send_json"), \
             patch.object(agent_worker, "send_browser_callback", side_effect=RuntimeError("callback")), \
             patch.object(agent_worker, "dispatch_next_queued"), \
             patch.object(agent_worker, "emit_lifecycle", side_effect=lambda text: _safe_console_write(text + "\n", output)):
            agent_worker.execute_claimed_job(job, Mock())
        self.assertEqual(output.getvalue().count("JOB START"), 1)
        self.assertEqual(output.getvalue().count("JOB END"), 1)

    def test_utf8_and_redirected_streams_flush(self):
        for stream in (io.StringIO(), NonTtyStream()):
            _safe_console_write("JOB START 日本語\n", stream)
            self.assertEqual(stream.getvalue(), "JOB START 日本語\n")

    def test_unicode_encode_error_cannot_suppress_lifecycle_marker(self):
        stream = AsciiStream()
        _safe_console_write("actor text 日本語; JOB END\n", stream)
        self.assertIn("JOB END", stream.getvalue())
        self.assertIn("\\u65e5", stream.getvalue())

    def test_lifecycle_logger_is_captured_by_non_console_handler(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        lifecycle_logger = logging.getLogger("local_agent.lifecycle")
        lifecycle_logger.addHandler(handler)
        lifecycle_logger.setLevel(logging.INFO)
        lifecycle_logger.propagate = False
        try:
            with patch("sys.stdout", NonTtyStream()):
                emit_lifecycle("JOB START worker")
        finally:
            lifecycle_logger.removeHandler(handler)
            lifecycle_logger.propagate = True
        self.assertIn("JOB START worker", stream.getvalue())

    def test_configured_sink_survives_transient_stdout_replacement(self):
        visible = NonTtyStream()
        captured = NonTtyStream()
        old_sink = operational_logging._console_sink
        try:
            operational_logging._console_sink = visible
            with patch("sys.stdout", captured):
                emit_lifecycle("JOB START 日本語")
        finally:
            operational_logging._console_sink = old_sink
        self.assertIn("JOB START 日本語", visible.getvalue())
        self.assertEqual(captured.getvalue(), "")


class ProductionPathConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(__file__).parents[1] / ".tmp-tests" / f"console-{uuid.uuid4().hex}"
        self.temp_dir.mkdir(parents=True)
        self.db_path = self.temp_dir / "state.db"
        self.connections = []

        def connect():
            db = sqlite3.connect(self.db_path, check_same_thread=False)
            db.row_factory = sqlite3.Row
            self.connections.append(db)
            return db

        self.connect_patch = patch.object(agent_worker.state_store, "connect", connect)
        self.connect_patch.start()
        agent_worker.state_store.initialize()

    def tearDown(self):
        self.connect_patch.stop()
        for db in self.connections:
            db.close()
        self.db_path.unlink(missing_ok=True)
        self.temp_dir.rmdir()

    def run_registered_path(self, actor, callback_fails=False):
        job_id = f"PROD-{actor.upper()}-日本語"
        payload = {
            "protocol_version": "1",
            "job_id": job_id,
            "actor": actor,
            "mode": "implementation" if actor == "codex" else "review",
            "workspace": "sandbox",
            "prompt": "production path",
        }
        visible = NonTtyStream()
        transient = NonTtyStream()
        actor_transcript = "actor transcript: no lifecycle markers"
        done = threading.Event()
        old_sink = operational_logging._console_sink
        original_dispatch = agent_worker.dispatch_next_queued

        bridge = slack_bridge.SlackBridge.__new__(slack_bridge.SlackBridge)
        bridge.message_handler = agent_worker.process_message

        def fake_execute(job, say):
            self.assertEqual(agent_worker.state_store.get_job(job.job_id)["status"], "RUNNING")
            agent_worker.state_store.mark_completed(job.job_id, status="DONE", exit_code=0)
            if callback_fails:
                try:
                    agent_worker.send_browser_callback(
                        job, status="DONE", artifact_status="DONE"
                    )
                except RuntimeError:
                    pass

        def terminal_dispatch(workspace, say):
            if agent_worker.state_store.get_job(job_id)["status"] == "DONE":
                done.set()

        try:
            operational_logging._console_sink = visible
            with patch.object(slack_bridge, "ALLOWED_CHANNEL_IDS", {"C"}), \
                 patch.object(slack_bridge, "ALLOWED_SENDER_IDS", {"U"}), \
                 patch.object(agent_worker, "execute_job", side_effect=fake_execute), \
                 patch.object(agent_worker, "dispatch_next_queued") as dispatch, \
                 patch.object(agent_worker, "send_json"), \
                 patch.object(agent_worker, "send_browser_callback", side_effect=RuntimeError("callback failed") if callback_fails else None), \
                 patch("sys.stdout", transient):
                # Keep the real registered handler -> process_message -> queue ->
                # dispatch -> production thread target.  Only execution's
                # external actor/callback boundary is substituted.
                def observed_dispatch(workspace, say):
                    original_dispatch(workspace, say)
                    terminal_dispatch(workspace, say)

                dispatch.side_effect = observed_dispatch
                bridge._handle_message({"channel": "C", "user": "U", "text": json.dumps(payload)}, Mock())
                self.assertTrue(done.wait(2), "production worker thread did not finish")
        finally:
            operational_logging._console_sink = old_sink

        row = agent_worker.state_store.get_job(job_id)
        output = visible.getvalue()
        self.assertEqual(row["status"], "DONE")
        self.assertIsNotNone(row["started_at"])
        self.assertEqual(output.count("JOB START"), 1)
        self.assertEqual(output.count("JOB END"), 1)
        self.assertNotIn("JOB START", transient.getvalue())
        self.assertNotIn("JOB END", transient.getvalue())
        self.assertNotIn("JOB START", actor_transcript)
        self.assertNotIn("JOB END", actor_transcript)
        return output

    def test_registered_bolt_path_codex_uses_visible_non_tty_sink(self):
        output = self.run_registered_path("codex")
        self.assertIn("PROD-CODEX-日本語", output)

    def test_registered_bolt_path_claude_callback_failure_still_ends(self):
        output = self.run_registered_path("claude", callback_fails=True)
        self.assertIn("PROD-CLAUDE-日本語", output)


if __name__ == "__main__":
    unittest.main()
