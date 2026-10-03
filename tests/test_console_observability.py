import io
import logging
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_worker
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


if __name__ == "__main__":
    unittest.main()
