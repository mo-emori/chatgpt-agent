import json
import subprocess
import tempfile
import unittest
from pathlib import Path
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


def parse_slack_payload(message):
    prefix = "```json\n"
    suffix = "\n```"
    assert message.startswith(prefix)
    assert message.endswith(suffix)
    return json.loads(message[len(prefix):-len(suffix)])


class ExecuteJobRefactorTests(unittest.TestCase):
    @patch.object(agent_worker, "notify_chatgpt")
    def test_browser_callback_success_log_requires_notify_return(self, notify):
        job = make_job()

        with self.assertLogs(agent_worker.logger, level="INFO") as captured:
            result = agent_worker.send_browser_callback(
                job, status="DONE", artifact_status="DONE"
            )

        self.assertEqual(result["status"], "DONE")
        self.assertTrue(any(
            "Browser callback succeeded" in line for line in captured.output
        ))
        notify.assert_called_once()

    @patch.object(agent_worker, "notify_chatgpt")
    def test_delivery_unknown_is_failed_and_never_logs_success(self, notify):
        job = make_job()
        notify.side_effect = agent_worker.BrowserNotifyError(
            "DELIVERY_UNKNOWN: DELIVERY_ACK_TIMEOUT"
        )

        with self.assertLogs(agent_worker.logger, level="INFO") as captured:
            result = agent_worker.send_browser_callback(
                job, status="DONE", artifact_status="DONE"
            )

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error"], "DELIVERY_UNKNOWN: DELIVERY_ACK_TIMEOUT")
        self.assertFalse(any(
            "Browser callback succeeded" in line for line in captured.output
        ))
        notify.assert_called_once()

    @patch.object(agent_worker, "dispatch_next_queued")
    @patch.object(agent_worker, "finalize_browser_callback")
    @patch.object(agent_worker, "publish_slack_result")
    @patch.object(agent_worker.state_store, "mark_completed")
    @patch.object(agent_worker, "process_artifacts")
    @patch.object(agent_worker, "collect_execution_evidence")
    @patch.object(agent_worker, "collect_runtime_evidence")
    @patch.object(agent_worker, "run_agent")
    @patch.object(agent_worker, "prepare_execution")
    def test_success_keeps_execution_and_artifact_status_separate(
        self,
        prepare,
        run_agent,
        collect_runtime,
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
    @patch.object(agent_worker, "notify_chatgpt")
    @patch.object(agent_worker.state_store, "mark_completed")
    @patch.object(agent_worker, "run_agent")
    @patch.object(agent_worker, "prepare_execution")
    def test_bridge_error_publishes_before_failed_callback_and_saves_final_snapshot(
        self,
        prepare,
        run_agent,
        mark_completed,
        notify,
        dispatch,
    ):
        job = make_job()
        events = []
        slack_messages = []

        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            prepare.return_value = ({}, "workdir", log_dir, {"head": "before"})
            run_agent.side_effect = RuntimeError("bridge exploded")
            notify.side_effect = lambda **kwargs: (
                events.append("callback")
                or (_ for _ in ()).throw(
                    agent_worker.BrowserNotifyError("callback unavailable")
                )
            )

            def say(message):
                events.append("slack")
                slack_messages.append(message)

            agent_worker.execute_job(job, say)

            local_result = json.loads(
                (log_dir / "result.json").read_text(encoding="utf-8")
            )

        expected_slack = {
            "protocol_version": "1",
            "job_id": "JOB-001",
            "actor": "claude",
            "mode": "review",
            "workspace": "argus",
            "prompt_sha256": "a" * 64,
            "status": "BRIDGE_ERROR",
            "failure_class": "BRIDGE_ERROR",
            "error_summary": "bridge exploded",
            "runtime": parse_slack_payload(slack_messages[0])["runtime"],
        }
        self.assertEqual(events, ["slack", "callback"])
        self.assertEqual(parse_slack_payload(slack_messages[0]), expected_slack)
        self.assertEqual(
            local_result,
            {
                **expected_slack,
                "callback": {
                    "type": "chatgpt_browser",
                    "status": "FAILED",
                    "error": "callback unavailable",
                    "url": job.callback_url,
                },
            },
        )
        mark_completed.assert_called_once_with(
            job.job_id,
            status="FAILED",
            failure_class="BRIDGE_ERROR",
        )
        dispatch.assert_called_once_with(job.workspace, say)

    @patch.object(agent_worker, "dispatch_next_queued")
    @patch.object(agent_worker, "notify_chatgpt")
    @patch.object(agent_worker.state_store, "mark_completed")
    @patch.object(agent_worker, "collect_execution_evidence")
    @patch.object(agent_worker, "collect_runtime_evidence")
    @patch.object(agent_worker, "run_agent")
    @patch.object(agent_worker, "prepare_execution")
    def test_actor_failure_keeps_git_evidence_and_isolates_callback_failure(
        self,
        prepare,
        run_agent,
        collect_runtime,
        collect_evidence,
        mark_completed,
        notify,
        dispatch,
    ):
        job = make_job()
        before = {"head": "abc123"}
        after = {"head": "def456"}
        actor_result = SimpleNamespace(
            returncode=17,
            stdout="actor output",
            stderr="actor error",
        )
        runtime = {
            "actor": "claude", "cli_version": "2.1.280 (Claude Code)",
            "configured_default_model": None, "requested_model": None,
            "effective_model": None, "sandbox_config": None,
        }
        collect_runtime.return_value = runtime
        events = []
        slack_messages = []

        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            prepare.return_value = (
                {"artifact_roots": ["artifacts"]},
                "workdir",
                log_dir,
                before,
            )
            run_agent.return_value = actor_result
            collect_evidence.return_value = (
                after,
                ["agent_worker.py", "tests/test_agent_worker_refactor.py"],
            )
            notify.side_effect = lambda **kwargs: (
                events.append("callback")
                or (_ for _ in ()).throw(
                    agent_worker.BrowserNotifyError("browser offline")
                )
            )

            def say(message):
                events.append("slack")
                slack_messages.append(message)

            agent_worker.execute_job(job, say)
            local_result = json.loads(
                (log_dir / "result.json").read_text(encoding="utf-8")
            )

        expected_slack = {
            "protocol_version": "1",
            "job_id": "JOB-001",
            "actor": "claude",
            "mode": "review",
            "workspace": "argus",
            "prompt_sha256": "a" * 64,
            "status": "FAILED",
            "exit_code": 17,
            "failure_class": "UNKNOWN_RUNTIME_FAILURE",
            "error_summary": "actor error",
            "runtime": runtime,
            "runtime_diagnostics": {
                "classification": "UNKNOWN_RUNTIME_FAILURE",
                "scope": "unknown; inspect raw error",
                "repair_hint": "Diagnose the raw actor error before changing configuration, then smoke-test and retry.",
            },
            "git": {
                "baseline_commit": "abc123",
                "head_after": "def456",
                "changed_paths": [
                    "agent_worker.py",
                    "tests/test_agent_worker_refactor.py",
                ],
            },
        }
        self.assertEqual(events, ["slack", "callback"])
        self.assertEqual(parse_slack_payload(slack_messages[0]), expected_slack)
        self.assertNotIn("callback", expected_slack)
        self.assertEqual(
            local_result,
            {
                **expected_slack,
                "callback": {
                    "type": "chatgpt_browser",
                    "status": "FAILED",
                    "error": "browser offline",
                    "url": job.callback_url,
                },
            },
        )
        collect_evidence.assert_called_once_with(
            actor_result,
            workdir="workdir",
            log_dir=log_dir,
            before=before,
        )
        mark_completed.assert_called_once_with(
            job.job_id,
            status="FAILED",
            exit_code=17,
            failure_class="UNKNOWN_RUNTIME_FAILURE",
        )
        dispatch.assert_called_once_with(job.workspace, say)

    @patch.object(agent_worker, "dispatch_next_queued")
    @patch.object(agent_worker, "notify_chatgpt")
    @patch.object(agent_worker.state_store, "mark_completed")
    @patch.object(agent_worker, "process_artifacts")
    @patch.object(agent_worker, "collect_execution_evidence")
    @patch.object(agent_worker, "collect_runtime_evidence")
    @patch.object(agent_worker, "run_agent")
    @patch.object(agent_worker, "prepare_execution")
    def test_success_slack_and_local_result_snapshots_keep_all_status_domains(
        self,
        prepare,
        run_agent,
        collect_runtime,
        collect_evidence,
        process_artifacts,
        mark_completed,
        notify,
        dispatch,
    ):
        job = make_job()
        before = {"head": "base"}
        after = {"head": "head"}
        actor_result = SimpleNamespace(returncode=0, stdout="output", stderr="")
        runtime = {
            "actor": "claude", "cli_version": "2.1.280 (Claude Code)",
            "configured_default_model": None, "requested_model": None,
            "effective_model": None, "sandbox_config": None,
        }
        collect_runtime.return_value = runtime
        events = []
        slack_messages = []

        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir)
            prepare.return_value = (
                {"artifact_roots": ["artifacts"]},
                "workdir",
                log_dir,
                before,
            )
            run_agent.return_value = actor_result
            collect_evidence.return_value = (after, ["artifacts/report.txt"])
            process_artifacts.return_value = (
                "completed with one rejection",
                "PARTIAL_FAILURE",
                [{
                    "name": "report.txt",
                    "source_path": "artifacts/report.txt",
                    "drive_file_id": "drive-1",
                }],
                [{"path": "notes.txt", "reason": "outside allowed roots"}],
            )

            def notify_callback(**kwargs):
                events.append("callback")

            notify.side_effect = notify_callback

            def say(message):
                events.append("slack")
                slack_messages.append(message)

            agent_worker.execute_job(job, say)
            local_result = json.loads(
                (log_dir / "result.json").read_text(encoding="utf-8")
            )

        expected_slack = {
            "protocol_version": "1",
            "job_id": "JOB-001",
            "actor": "claude",
            "mode": "review",
            "workspace": "argus",
            "prompt_sha256": "a" * 64,
            "status": "DONE",
            "runtime": runtime,
            "exit_code": 0,
            "summary": "completed with one rejection",
            "git": {
                "baseline_commit": "base",
                "head_after": "head",
                "changed_paths": ["artifacts/report.txt"],
            },
            "artifact_status": "PARTIAL_FAILURE",
            "artifacts": [{
                "name": "report.txt",
                "source_path": "artifacts/report.txt",
                "drive_file_id": "drive-1",
            }],
            "rejected_artifacts": [{
                "path": "notes.txt",
                "reason": "outside allowed roots",
            }],
        }
        self.assertEqual(events, ["slack", "callback"])
        self.assertEqual(parse_slack_payload(slack_messages[0]), expected_slack)
        self.assertNotIn("callback", expected_slack)
        self.assertEqual(
            local_result,
            {
                **expected_slack,
                "callback": {
                    "type": "chatgpt_browser",
                    "status": "DONE",
                    "url": job.callback_url,
                },
            },
        )
        mark_completed.assert_called_once_with(
            job.job_id,
            status="DONE",
            exit_code=0,
            failure_class="ARTIFACT_ERROR",
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


class ProcessArtifactsTests(unittest.TestCase):
    def make_manifest(self, summary, artifacts):
        return (
            "actor output\n<AGENT_RESULT>\n"
            + json.dumps({"summary": summary, "artifacts": artifacts})
            + "\n</AGENT_RESULT>"
        )

    @patch.object(agent_worker, "upload_artifacts")
    def test_valid_artifact_is_uploaded_and_preserved(self, upload):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "artifacts" / "report.txt"
            artifact.parent.mkdir()
            artifact.write_text("report", encoding="utf-8")
            upload.return_value = [{
                "name": "report.txt",
                "drive_file_id": "drive-1",
            }]
            result = SimpleNamespace(
                stdout=self.make_manifest("complete", ["artifacts/report.txt"])
            )

            actual = agent_worker.process_artifacts(
                make_job(), result, {"artifact_roots": ["artifacts"]}, root
            )

        self.assertEqual(actual, (
            "complete",
            "DONE",
            [{
                "name": "report.txt",
                "source_path": "artifacts/report.txt",
                "drive_file_id": "drive-1",
            }],
            [],
        ))
        upload.assert_called_once_with("JOB-001", [artifact.resolve()])

    @patch.object(agent_worker, "upload_artifacts")
    def test_rejected_artifact_reports_failed_without_upload(self, upload):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = SimpleNamespace(
                stdout=self.make_manifest("invalid", ["outside/report.txt"])
            )
            actual = agent_worker.process_artifacts(
                make_job(), result, {"artifact_roots": ["artifacts"]}, root
            )

        self.assertEqual(actual[0:3], ("invalid", "FAILED", []))
        self.assertEqual(actual[3][0]["path"], "outside/report.txt")
        self.assertIn("outside allowed roots", actual[3][0]["reason"])
        upload.assert_not_called()

    @patch.object(agent_worker, "upload_artifacts")
    def test_multiple_artifacts_keep_manifest_order(self, upload):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = ["artifacts/first.txt", "artifacts/second.txt"]
            for relative_path in paths:
                path = root / relative_path
                path.parent.mkdir(exist_ok=True)
                path.write_text(relative_path, encoding="utf-8")
            upload.return_value = [
                {"name": "first.txt", "drive_file_id": "drive-1"},
                {"name": "second.txt", "drive_file_id": "drive-2"},
            ]
            result = SimpleNamespace(
                stdout=self.make_manifest("two files", paths)
            )
            actual = agent_worker.process_artifacts(
                make_job(), result, {"artifact_roots": ["artifacts"]}, root
            )

        self.assertEqual(actual, (
            "two files",
            "DONE",
            [
                {
                    "name": "first.txt",
                    "source_path": "artifacts/first.txt",
                    "drive_file_id": "drive-1",
                },
                {
                    "name": "second.txt",
                    "source_path": "artifacts/second.txt",
                    "drive_file_id": "drive-2",
                },
            ],
            [],
        ))

    @patch.object(agent_worker, "upload_artifacts")
    def test_drive_upload_failure_stays_in_artifact_status_domain(self, upload):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "artifacts" / "report.txt"
            artifact.parent.mkdir()
            artifact.write_text("report", encoding="utf-8")
            upload.side_effect = RuntimeError("Drive unavailable")
            result = SimpleNamespace(
                stdout=self.make_manifest("complete", ["artifacts/report.txt"])
            )
            actual = agent_worker.process_artifacts(
                make_job(), result, {"artifact_roots": ["artifacts"]}, root
            )

        self.assertEqual(actual, (
            "complete",
            "FAILED",
            [],
            [{
                "path": None,
                "reason": "Artifact processing error: Drive unavailable",
            }],
        ))


if __name__ == "__main__":
    unittest.main()
