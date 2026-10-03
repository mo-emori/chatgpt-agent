import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import agent_worker
from single_instance import SingleInstanceAlreadyRunning, WorkerInstanceGuard


class SingleInstanceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).parents[1] / ".tmp-tests")
        self.lock_path = Path(self.temp_dir.name) / "worker.lock"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_first_acquires_second_is_rejected_then_release_allows_acquire(self):
        first = WorkerInstanceGuard(self.lock_path).acquire()
        try:
            with self.assertRaisesRegex(
                SingleInstanceAlreadyRunning, "SINGLE_INSTANCE_ALREADY_RUNNING"
            ):
                WorkerInstanceGuard(self.lock_path).acquire()
        finally:
            first.release()

        replacement = WorkerInstanceGuard(self.lock_path).acquire()
        replacement.release()

    def test_abnormal_process_death_releases_os_lock_despite_stale_metadata(self):
        script = (
            "import os,sys; "
            "from single_instance import WorkerInstanceGuard; "
            "WorkerInstanceGuard(sys.argv[1]).acquire(); os._exit(0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.lock_path)],
            cwd=Path(__file__).parents[1],
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.lock_path.exists())
        recovered = WorkerInstanceGuard(self.lock_path).acquire()
        recovered.release()

    @patch.object(agent_worker, "SlackBridge")
    @patch.object(agent_worker, "WorkerInstanceGuard")
    @patch.object(agent_worker, "check_cli_versions")
    @patch.object(agent_worker.state_store, "initialize")
    @patch.object(agent_worker, "configure_logging")
    def test_rejection_precedes_bridge_and_recovery(
        self, configure, initialize, check_cli, guard_class, bridge_class
    ):
        guard_class.return_value.acquire.side_effect = SingleInstanceAlreadyRunning(
            "SINGLE_INSTANCE_ALREADY_RUNNING"
        )
        with patch.object(agent_worker, "recover_running_jobs") as running, \
             patch.object(agent_worker, "recover_queued_jobs") as queued:
            result = agent_worker.main([])
        self.assertEqual(result, 2)
        bridge_class.assert_not_called()
        running.assert_not_called()
        queued.assert_not_called()

    @patch.object(agent_worker, "run_checks", return_value=0)
    @patch.object(agent_worker, "WorkerInstanceGuard")
    def test_non_daemon_check_does_not_acquire_guard(self, guard_class, run_checks):
        self.assertEqual(agent_worker.main(["--check", "codex"]), 0)
        guard_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
