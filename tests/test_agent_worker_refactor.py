import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_worker


def make_job():
    return SimpleNamespace(
        protocol_version="1",
        job_id="JOB-001",
        actor="claude",
        mode="review",
        workspace="argus",
        prompt_sha256="a" * 64,
        callback_type="chatgpt_browser",
        callback_url="https://example.invalid/callback",
    )


class ExecuteJobRefactorTests(unittest.TestCase):
    @patch.object(agent_worker, "dispatch_next_queued")
    @patch.object(agent_worker, "finalize_browser_callback")
    @patch.object(agent_worker, "publish_slack_result")
    @patch.object(agent_worker.state_store, "mark_completed")
    @patch.object(agent_worker, "process_artifacts")
    @patch.object(agent_worker, "collect_execution_evidence")
    @patch.object(agent_worker, "run_agent")
    @patch.object(agent_worker, "prepare_execution")
    def test_success_keeps_execution_and_artifact_status_separate(
        self,
        prepare,
        run_agent,
        collect_evidence,
        process_artifacts,
        mark_completed,
        publish,
        finalize_callback,
        dispatch,
    ):
        job = make_job()
        say = Mock()
        before = {"head": "before"}
        after = {"head": "after"}
        result = SimpleNamespace(returncode=0, stdout="output", stderr="")
        prepare.return_value = (
            {"artifact_roots": ["artifacts"]},
            "workdir",
            "logdir",
            before,
        )
        run_agent.return_value = result
        collect_evidence.return_value = (after, ["agent_worker.py"])
        process_artifacts.return_value = (
            "summary",
            "FAILED",
            [],
            [{"path": None, "reason": "upload failed"}],
        )

        agent_worker.execute_job(job, say)

        mark_completed.assert_called_once_with(
            job.job_id,
            status="DONE",
            exit_code=0,
            failure_class="ARTIFACT_ERROR",
        )
        response = publish.call_args.args[2]
        self.assertEqual(response["status"], "DONE")
        self.assertEqual(response["artifact_status"], "FAILED")
        self.assertEqual(response["prompt_sha256"], job.prompt_sha256)
        finalize_callback.assert_called_once_with(
            job,
            "logdir",
            response,
            status="DONE",
            artifact_status="FAILED",
        )
        dispatch.assert_called_once_with(job.workspace, say)

    @patch.object(agent_worker, "dispatch_next_queued")
    @patch.object(agent_worker, "handle_execution_failure")
    @patch.object(agent_worker, "run_agent")
    @patch.object(agent_worker, "prepare_execution")
    def test_timeout_uses_failure_path_and_still_dispatches(
        self,
        prepare,
        run_agent,
        handle_failure,
        dispatch,
    ):
        job = make_job()
        say = Mock()
        prepare.return_value = ({}, "workdir", "logdir", {"head": "before"})
        run_agent.side_effect = subprocess.TimeoutExpired("actor", 30)

        agent_worker.execute_job(job, say)

        handle_failure.assert_called_once_with(
            job,
            say,
            "logdir",
            failure_class="TIMEOUT",
            response_status="FAILED",
            artifact_status="NOT_RUN",
        )
        dispatch.assert_called_once_with(job.workspace, say)


class PublicationOrderTests(unittest.TestCase):
    @patch.object(agent_worker, "send_browser_callback")
    @patch.object(agent_worker, "send_json")
    @patch.object(agent_worker, "save_json")
    def test_slack_result_precedes_browser_callback(
        self,
        save_json,
        send_json,
        send_callback,
    ):
        events = []
        save_json.side_effect = lambda *args: events.append("save")
        send_json.side_effect = lambda *args: events.append("slack")
        send_callback.side_effect = lambda *args, **kwargs: (
            events.append("callback") or {"status": "DONE"}
        )
        response = {"status": "DONE"}

        agent_worker.publish_slack_result("logdir", Mock(), response)
        agent_worker.finalize_browser_callback(
            make_job(),
            "logdir",
            response,
            status="DONE",
            artifact_status="DONE",
        )

        self.assertEqual(events, ["save", "slack", "callback", "save"])


if __name__ == "__main__":
    unittest.main()
