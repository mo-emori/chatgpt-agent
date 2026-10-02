import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import agent_worker
import state_store
from job_protocol import Job


NativeThread = threading.Thread


class ImmediateThread:
    starts = []

    def __init__(self, *, target, args, daemon):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        self.starts.append(self.args[0])


class QueueRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "state.db"
        self.connections = []

        def tracked_connect():
            db = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
            )
            db.row_factory = sqlite3.Row
            self.connections.append(db)
            return db

        self.connect_patch = patch.object(
            state_store,
            "connect",
            tracked_connect,
        )
        self.connect_patch.start()
        state_store.initialize()
        ImmediateThread.starts = []
        self.say = Mock()

    def tearDown(self):
        self.connect_patch.stop()
        for db in self.connections:
            db.close()
        self.temp_dir.cleanup()

    def queue_job(
        self,
        job_id,
        *,
        workspace="sandbox",
        prompt=None,
        protocol_version="3",
    ):
        prompt = prompt or f"accepted snapshot for {job_id}"
        ref = (
            {"type": "notion_page", "page_id": f"page-{job_id}"}
            if protocol_version == "3"
            else None
        )
        job = Job(
            protocol_version=protocol_version,
            job_id=job_id,
            actor="claude",
            mode="review",
            workspace=workspace,
            prompt=prompt,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            instruction_ref=ref,
            callback_type="chatgpt_browser",
            callback_url="https://chatgpt.com/c/test",
        )
        state_store.create_job(job)
        state_store.mark_queued(job_id)
        return job

    @patch.object(agent_worker.threading, "Thread", ImmediateThread)
    def test_queued_job_survives_restart_and_is_selected(self):
        expected = self.queue_job("Q-1")

        agent_worker.recover_queued_jobs(self.say)

        self.assertEqual([job.job_id for job in ImmediateThread.starts], ["Q-1"])
        recovered = ImmediateThread.starts[0]
        self.assertEqual(recovered.prompt, expected.prompt)
        self.assertEqual(recovered.prompt_sha256, expected.prompt_sha256)
        self.assertEqual(recovered.instruction_ref, expected.instruction_ref)

    @patch.object(agent_worker.threading, "Thread", ImmediateThread)
    def test_fifo_head_only_is_started_for_one_workspace(self):
        self.queue_job("Q-FIRST")
        self.queue_job("Q-SECOND")

        agent_worker.recover_queued_jobs(self.say)

        self.assertEqual(
            [job.job_id for job in ImmediateThread.starts],
            ["Q-FIRST"],
        )

    @patch.object(agent_worker.threading, "Thread", ImmediateThread)
    def test_concurrent_dispatch_claims_and_starts_only_one_job(self):
        self.queue_job("Q-FIRST")
        self.queue_job("Q-SECOND")
        barrier = threading.Barrier(3)

        def dispatch():
            barrier.wait()
            agent_worker.dispatch_next_queued("sandbox", self.say)

        callers = [NativeThread(target=dispatch) for _ in range(2)]
        for caller in callers:
            caller.start()
        barrier.wait()
        for caller in callers:
            caller.join()

        self.assertEqual(
            [job.job_id for job in ImmediateThread.starts],
            ["Q-FIRST"],
        )
        self.assertEqual(
            state_store.get_job("Q-FIRST")["status"],
            "DISPATCHING",
        )
        self.assertEqual(
            state_store.get_job("Q-SECOND")["status"],
            "QUEUED",
        )

    def test_manual_adoption_and_normal_dispatch_share_workspace_lease(self):
        self.queue_job("Q-LEASE")
        self.assertTrue(state_store.acquire_manual_workspace_claim("sandbox", "manual:1"))
        self.assertIsNone(state_store.claim_next_queued("sandbox"))
        self.assertFalse(state_store.acquire_manual_workspace_claim("sandbox", "manual:2"))
        self.assertTrue(state_store.release_manual_workspace_claim("sandbox", "manual:1"))
        claimed = state_store.claim_next_queued("sandbox")
        self.assertEqual(claimed["job_id"], "Q-LEASE")
        self.assertFalse(state_store.acquire_manual_workspace_claim("sandbox", "manual:3"))
        state_store.mark_completed("Q-LEASE", status="DONE")
        self.assertTrue(state_store.acquire_manual_workspace_claim("sandbox", "manual:3"))
        self.assertTrue(state_store.release_manual_workspace_claim("sandbox", "manual:3"))

    @patch.object(agent_worker.threading, "Thread", ImmediateThread)
    def test_concurrent_process_messages_atomically_dispatch_one_job(self):
        dispatch_barrier = threading.Barrier(2)
        original_dispatch = agent_worker.dispatch_next_queued
        messages = []
        messages_lock = threading.Lock()

        def synchronized_dispatch(workspace, say):
            dispatch_barrier.wait()
            original_dispatch(workspace, say)

        def say(message):
            with messages_lock:
                messages.append(json.loads(message[8:-4]))

        def accept(job_id):
            payload = {
                "protocol_version": "1",
                "job_id": job_id,
                "actor": "claude",
                "mode": "review",
                "workspace": "sandbox",
                "prompt": f"prompt for {job_id}",
            }
            agent_worker.process_message(
                text=json.dumps(payload),
                channel="channel",
                sender="sender",
                say=say,
            )

        with patch.object(
            agent_worker,
            "dispatch_next_queued",
            side_effect=synchronized_dispatch,
        ):
            callers = [
                NativeThread(target=accept, args=(job_id,))
                for job_id in ("NEW-FIRST", "NEW-SECOND")
            ]
            for caller in callers:
                caller.start()
            for caller in callers:
                caller.join()

        rows = [
            state_store.get_job(job_id)
            for job_id in ("NEW-FIRST", "NEW-SECOND")
        ]
        self.assertEqual(
            sorted(row["status"] for row in rows),
            ["DISPATCHING", "QUEUED"],
        )
        self.assertEqual(len(ImmediateThread.starts), 1)
        self.assertEqual(
            ImmediateThread.starts[0].job_id,
            next(
                row["job_id"]
                for row in rows
                if row["status"] == "DISPATCHING"
            ),
        )
        queued_job_id = next(
            row["job_id"]
            for row in rows
            if row["status"] == "QUEUED"
        )
        self.assertEqual(
            [message["job_id"] for message in messages],
            [queued_job_id],
        )
        self.assertEqual(messages[0]["status"], "QUEUED")

    @patch.object(agent_worker.threading, "Thread", ImmediateThread)
    def test_available_workspaces_each_recover_one_head(self):
        self.queue_job("Q-SANDBOX-1", workspace="sandbox")
        self.queue_job("Q-SANDBOX-2", workspace="sandbox")
        self.queue_job("Q-LOCAL-1", workspace="local-agent")

        agent_worker.recover_queued_jobs(self.say)

        self.assertEqual(
            [job.job_id for job in ImmediateThread.starts],
            ["Q-SANDBOX-1", "Q-LOCAL-1"],
        )

    @patch.object(agent_worker.threading, "Thread", ImmediateThread)
    def test_running_workspace_is_not_dispatched(self):
        running = self.queue_job("RUNNING", workspace="sandbox")
        state_store.mark_running(running.job_id, 1234, "test-host")
        self.queue_job("Q-BLOCKED", workspace="sandbox")

        agent_worker.recover_queued_jobs(self.say)

        self.assertEqual(ImmediateThread.starts, [])

    @patch.object(agent_worker, "fetch_instruction")
    @patch.object(agent_worker.threading, "Thread", ImmediateThread)
    def test_recovered_v3_uses_snapshot_without_notion(self, fetch_instruction):
        expected = self.queue_job("Q-SNAPSHOT", prompt="original bytes")

        agent_worker.recover_queued_jobs(self.say)

        fetch_instruction.assert_not_called()
        self.assertEqual(ImmediateThread.starts[0].prompt, "original bytes")
        self.assertEqual(
            ImmediateThread.starts[0].prompt_sha256,
            expected.prompt_sha256,
        )

    @patch.object(agent_worker, "fetch_instruction")
    @patch.object(agent_worker, "run_agent")
    @patch.object(agent_worker, "notify_chatgpt")
    @patch.object(agent_worker.threading, "Thread", ImmediateThread)
    def test_incomplete_legacy_v3_fails_closed(
        self, notify_chatgpt, run_agent, fetch_instruction
    ):
        self.queue_job("Q-INCOMPLETE")
        with state_store.connect() as db:
            db.execute(
                "UPDATE jobs SET prompt_sha256 = NULL WHERE job_id = ?",
                ("Q-INCOMPLETE",),
            )

        agent_worker.recover_queued_jobs(self.say)

        row = state_store.get_job("Q-INCOMPLETE")
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["failure_class"], "INCOMPLETE_QUEUED_STATE")
        self.assertEqual(ImmediateThread.starts, [])
        fetch_instruction.assert_not_called()
        run_agent.assert_not_called()
        notify_chatgpt.assert_called_once()

    @patch.object(agent_worker, "resolve_v3_instruction")
    def test_duplicate_behavior_remains_unchanged(self, resolve_instruction):
        self.queue_job("Q-DUPLICATE")
        payload = {
            "protocol_version": "3",
            "job_id": "Q-DUPLICATE",
            "actor": "claude",
            "mode": "review",
            "workspace": "sandbox",
            "instruction_ref": {
                "type": "notion_page",
                "page_id": "changed-page",
            },
        }

        agent_worker.process_message(
            text=json.dumps(payload),
            channel="channel",
            sender="sender",
            say=self.say,
        )

        resolve_instruction.assert_not_called()
        response = json.loads(self.say.call_args.args[0][8:-4])
        self.assertEqual(response["status"], "DUPLICATE_JOB")
        self.assertEqual(response["existing_status"], "QUEUED")

    @patch.object(agent_worker, "SlackBridge")
    @patch.object(agent_worker, "recover_queued_jobs")
    @patch.object(agent_worker, "recover_running_jobs")
    @patch.object(agent_worker, "check_cli_versions")
    @patch.object(agent_worker.state_store, "initialize")
    def test_startup_recovers_running_before_queued(
        self,
        initialize,
        check_cli,
        recover_running,
        recover_queued,
        bridge_class,
    ):
        events = []
        initialize.side_effect = lambda: events.append("initialize")
        check_cli.side_effect = lambda: events.append("cli")
        recover_running.side_effect = lambda: events.append("running")
        recover_queued.side_effect = lambda say: events.append("queued")
        bridge_class.return_value.start.side_effect = lambda: events.append("start")

        agent_worker.main()

        self.assertEqual(
            events,
            ["initialize", "cli", "running", "queued", "start"],
        )


if __name__ == "__main__":
    unittest.main()
