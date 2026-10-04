import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_worker
from job_protocol import Job, JobValidationError, parse_job


def payload(action="TRUST_INSPECT", trust=None, **extra):
    value = {
        "protocol_version": "3",
        "job_id": "TRUST-CONTROL-1",
        "workspace": "local-agent",
        "operation": "TRUST_CONTROL",
        "control_action": action,
        "trust": trust or {"capability": "FOO-CAP"},
    }
    value.update(extra)
    return value


class TrustControlProtocolTests(unittest.TestCase):
    def test_closed_control_shape_has_no_actor_fallback(self):
        job = parse_job(json.dumps(payload()))
        self.assertIsNone(job.actor)
        self.assertIsNone(job.mode)
        self.assertIsNone(job.instruction_ref)
        self.assertEqual(job.control_action, "TRUST_INSPECT")

    def test_unknown_and_malformed_actions_reject(self):
        for value, reason in (
            (payload("RUN_ACTOR"), "UNKNOWN_CONTROL_ACTION"),
            (payload(trust={"capability": "FOO-CAP", "path": "C:/escape"}),
             "TRUST_REQUEST_UNKNOWN_FIELD"),
            (payload(actor="codex"), "CONTROL_ACTOR_FIELDS_NOT_ALLOWED"),
            (payload(skip_pre_actor=True), "CONTROL_REQUEST_UNKNOWN_FIELD"),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(
                    JobValidationError, reason):
                parse_job(json.dumps(value))

    def test_normal_job_cannot_carry_control_or_bypass_fields(self):
        normal = {
            "protocol_version": "3", "job_id": "NORMAL-1",
            "workspace": "local-agent", "actor": "codex",
            "mode": "implementation",
            "instruction_ref": {"type": "notion_page", "page_id": "page"},
            "control_action": "TRUST_INSPECT",
        }
        with self.assertRaisesRegex(JobValidationError, "CONTROL_FIELDS_NOT_ALLOWED"):
            parse_job(json.dumps(normal))

    def test_mutation_contract_requires_hashes_operator_and_reason(self):
        trust = {"capability": "FOO-CAP", "expected_candidate_sha256": "a" * 64,
                 "operator": "operator", "reason": "reviewed"}
        self.assertEqual(parse_job(json.dumps(payload("TRUST_ACCEPT", trust))).control_action,
                         "TRUST_ACCEPT")
        trust["expected_current_trusted_sha256"] = "not-a-hash"
        with self.assertRaisesRegex(JobValidationError, "TRUST_REQUEST_INVALID_SHA256"):
            parse_job(json.dumps(payload("TRUST_ACCEPT", trust)))


class TrustControlWorkerTests(unittest.TestCase):
    def assert_callback_marker(self, job, status, expected_marker):
        with patch.object(agent_worker, "notify_chatgpt", return_value="DONE") as notify:
            result = agent_worker.send_browser_callback(
                job, status=status, artifact_status="NOT_APPLICABLE",
                failure_class=("TRUST_CONTROL_REJECTED"
                               if status == "REJECTED" else None))
        self.assertEqual(result["status"], "DONE")
        message = notify.call_args.kwargs["message"]
        self.assertTrue(message.startswith(expected_marker + "\n"))
        self.assertIn(f"control_action: {job.control_action}", message)
        self.assertIn(f"status: {status}", message)
        if status == "REJECTED":
            self.assertIn("failure_class: TRUST_CONTROL_REJECTED", message)

    def test_inspected_callback_is_success_and_preserves_control_status(self):
        self.assert_callback_marker(
            parse_job(json.dumps(payload(callback={
                "type": "chatgpt_browser", "url": "https://chatgpt.com/c/test"}))),
            "INSPECTED", "LOCAL_AGENT_JOB_COMPLETED")

    def test_accepted_callbacks_are_success(self):
        for action, trust in (
            ("TRUST_ACCEPT", {"capability": "FOO-CAP",
                              "expected_candidate_sha256": "a" * 64,
                              "operator": "operator", "reason": "reviewed"}),
            ("TRUST_LEGACY_AUTO_MIGRATE", {"capability": "FOO-CAP",
                              "expected_candidate_sha256": "a" * 64,
                              "expected_legacy_trusted_sha256": "b" * 64,
                              "operator": "operator", "reason": "migration"}),
        ):
            with self.subTest(action=action):
                job = parse_job(json.dumps(payload(
                    action, trust, callback={"type": "chatgpt_browser",
                                             "url": "https://chatgpt.com/c/test"})))
                self.assert_callback_marker(
                    job, "ACCEPTED", "LOCAL_AGENT_JOB_COMPLETED")

    def test_rejected_control_callback_remains_failure(self):
        job = parse_job(json.dumps(payload(callback={
            "type": "chatgpt_browser", "url": "https://chatgpt.com/c/test"})))
        self.assert_callback_marker(
            job, "REJECTED", "LOCAL_AGENT_JOB_FAILED")

    def test_control_callback_absent_is_not_an_error(self):
        job = parse_job(json.dumps(payload()))
        with patch.object(agent_worker, "notify_chatgpt") as notify:
            result = agent_worker.send_browser_callback(
                job, status="INSPECTED", artifact_status="NOT_APPLICABLE")
        self.assertEqual(result, {"type": None, "status": "NOT_REQUESTED"})
        notify.assert_not_called()

    def test_normal_actor_non_done_status_remains_failure(self):
        job = Job(protocol_version="3", job_id="NORMAL-CALLBACK", actor="codex",
                  mode="implementation", workspace="local-agent",
                  instruction_ref={"type": "notion_page", "page_id": "page"},
                  callback_type="chatgpt_browser",
                  callback_url="https://chatgpt.com/c/test")
        with patch.object(agent_worker, "notify_chatgpt", return_value="DONE") as notify:
            agent_worker.send_browser_callback(
                job, status="ACCEPTED", artifact_status="DONE")
        self.assertTrue(notify.call_args.kwargs["message"].startswith(
            "LOCAL_AGENT_JOB_FAILED\n"))

    def test_inspect_dispatches_before_context_gate_and_never_starts_actor(self):
        job = parse_job(json.dumps(payload()))
        with tempfile.TemporaryDirectory(dir=".tmp-tests") as temp:
            published = []
            with patch.object(agent_worker, "WORKSPACES", {"local-agent": {
                    "path": Path(temp)}}), \
                 patch.object(agent_worker, "create_job_log", return_value=Path(temp)), \
                 patch.object(agent_worker.context_trust, "inspect_trust", return_value={
                    "status": "INSPECTED", "workspace": "local-agent",
                    "capability": "FOO-CAP", "candidate_fresh": False,
                    "validation_reason_codes": ["CANDIDATE_STALE"]}), \
                 patch.object(agent_worker, "begin_shadow") as begin_shadow, \
                 patch.object(agent_worker, "run_agent") as run_actor, \
                 patch.object(agent_worker.state_store, "mark_completed"), \
                 patch.object(agent_worker, "publish_slack_result",
                              side_effect=lambda _log, _say, result: published.append(result)), \
                 patch.object(agent_worker, "finalize_browser_callback") as callback:
                agent_worker.execute_job(job, lambda *_args, **_kwargs: None)
        begin_shadow.assert_not_called()
        run_actor.assert_not_called()
        callback.assert_called_once()
        result = published[0]
        self.assertEqual(result["operation"], "TRUST_CONTROL")
        self.assertFalse(result["actor_started"])
        self.assertIsNone(result["actor"])
        self.assertIsNone(result["effective_model"])
        self.assertNotIn("effective_input_sha256", result)
        self.assertNotIn("actor_input_sha256", result)

    def test_legacy_migration_uses_strict_tool_without_actor(self):
        trust = {"capability": "FOO-CAP", "expected_candidate_sha256": "a" * 64,
                 "expected_legacy_trusted_sha256": "b" * 64,
                 "operator": "operator", "reason": "legacy migration"}
        job = parse_job(json.dumps(payload("TRUST_LEGACY_AUTO_MIGRATE", trust)))
        with tempfile.TemporaryDirectory(dir=".tmp-tests") as temp, \
             patch.object(agent_worker, "WORKSPACES", {"local-agent": {
                "path": Path(temp)}}), \
             patch.object(agent_worker, "create_job_log", return_value=Path(temp)), \
             patch.object(agent_worker.context_trust, "legacy_auto_migrate",
                          return_value={"status": "ACCEPTED",
                                        "trusted_manifest_sha256": "a" * 64,
                                        "previous_trusted_manifest_sha256": "b" * 64,
                                        "receipt_path": "receipts/r.json",
                                        "archive_path": "archive/b.json"}) as migrate, \
             patch.object(agent_worker, "run_agent") as run_actor, \
             patch.object(agent_worker.state_store, "mark_completed"), \
             patch.object(agent_worker, "publish_slack_result") as publish, \
             patch.object(agent_worker, "finalize_browser_callback"):
            agent_worker.execute_job(job, lambda *_args, **_kwargs: None)
        migrate.assert_called_once()
        run_actor.assert_not_called()
        result = publish.call_args.args[2]
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertFalse(result["actor_started"])


if __name__ == "__main__":
    unittest.main()
