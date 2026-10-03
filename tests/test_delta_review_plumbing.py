import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import agent_worker
import review_invocation
import state_store
from job_protocol import JobValidationError, job_from_row, parse_job
from slack_bridge import SlackBridge


FIXTURE = {
    "protocol_version": "3",
    "job_id": "ARGUS-BOOTSTRAP-CONTEXT-HARNESS-DELTA-REVIEW-E2E-20261003-002",
    "actor": "claude",
    "mode": "review",
    "workspace": "argus",
    "review_mode": "DELTA_REVIEW",
    "review_package_ref": {
        "path": "validation/context/RUNTIME-BOOTSTRAP-ORCHESTRATOR/ARGUS-BOOTSTRAP-DELTA-REVIEW-READY-GATE-20261003-001/review-package",
        "sha256": "5f7fd6529e210fcae542057393636893d3ba8b216cc95b9fe8963e9f4aaab1e4",
        "target_job_id": "ARGUS-BOOTSTRAP-DELTA-REVIEW-READY-GATE-20261003-001",
    },
    "instruction_ref": {
        "type": "notion_page",
        "page_id": "3ee30bde-8a72-81f3-875b-ef2c511857b8",
    },
    "callback": {
        "type": "chatgpt_browser",
        "url": "https://chatgpt.com/c/6ab6c87f-0ca0-83e8-a2f7-ab969d4d8f65",
    },
}


class ValidatorReached(Exception):
    pass


class DeltaReviewPlumbingTests(unittest.TestCase):
    def parse(self, data=None):
        return parse_job(json.dumps(data or FIXTURE))

    def test_slack_json_and_protocol_normalization_preserve_exact_ref(self):
        forwarded = []
        bridge = SlackBridge.__new__(SlackBridge)
        bridge.message_handler = lambda **kwargs: forwarded.append(kwargs["text"])
        with patch("slack_bridge.ALLOWED_CHANNEL_IDS", {"C"}), \
             patch("slack_bridge.ALLOWED_SENDER_IDS", {"U"}):
            bridge._handle_message(
                {"channel": "C", "user": "U", "text": json.dumps(FIXTURE)},
                lambda message: None,
            )
        self.assertEqual(json.loads(forwarded[0])["review_package_ref"], FIXTURE["review_package_ref"])
        job = parse_job(forwarded[0])
        self.assertEqual(job.review_mode, "DELTA_REVIEW")
        self.assertEqual(job.review_package_ref, FIXTURE["review_package_ref"])
        self.assertTrue(job.measurement_mode)

    def test_state_round_trip_and_worker_request_preserve_exact_ref(self):
        # Production resolves instruction_ref to prompt before persistence.
        job = replace(self.parse(), prompt="resolved review instruction")
        temp_root = Path(__file__).parents[1] / ".tmp-tests"
        temp_root.mkdir(exist_ok=True)
        # sqlite context managers commit but do not close immediately on
        # Windows; tolerate deferred cleanup of this ignored test directory.
        with tempfile.TemporaryDirectory(dir=temp_root, ignore_cleanup_errors=True) as td:
            root = Path(td)
            with patch.object(state_store, "STATE_DB", root / "state.db"):
                state_store.initialize()
                state_store.create_job(job)
                restored = job_from_row(state_store.get_job(job.job_id))
            self.assertEqual(restored.review_mode, "DELTA_REVIEW")
            self.assertEqual(restored.review_package_ref, FIXTURE["review_package_ref"])
            self.assertTrue(restored.measurement_mode)
            with patch.dict(agent_worker.WORKSPACES, {"argus": {"path": root}}), \
                 patch.object(agent_worker, "get_git_snapshot", return_value={"head": "H"}), \
                 patch.object(agent_worker, "save_git_snapshot"):
                agent_worker.prepare_execution(restored, log_dir=root)
            request = json.loads((root / "request.json").read_text("utf-8"))
            self.assertEqual(request["review_package_ref"], FIXTURE["review_package_ref"])
            self.assertTrue(request["measurement_mode"])

    def test_exact_fixture_reaches_package_validator(self):
        job = self.parse()
        context = {"capability": "RUNTIME-BOOTSTRAP-ORCHESTRATOR"}
        policy = {"review_measurement": {
            "enabled": True,
            "capabilities": ["RUNTIME-BOOTSTRAP-ORCHESTRATOR"],
        }}
        observed = {"observed": {"unverifiable_reasons": []},
                    "lifecycle": {"manifest_sha256": "c" * 64}}
        with patch.object(review_invocation, "observe", return_value=observed), \
             patch.object(review_invocation, "validate_ref", side_effect=ValidatorReached) as validate:
            with self.assertRaises(ValidatorReached):
                review_invocation.prepare(job, Path("C:/dev/argus"), policy, context)
        self.assertEqual(validate.call_args.args[1], FIXTURE["review_package_ref"])

    def test_missing_and_malformed_refs_fail_closed(self):
        missing = dict(FIXTURE)
        missing.pop("review_package_ref")
        with self.assertRaisesRegex(JobValidationError, "DELTA_REVIEW_REQUIRES_MEASUREMENT_PACKAGE"):
            self.parse(missing)
        for key, value in (("sha256", "BAD"), ("path", ""), ("target_job_id", "")):
            malformed = json.loads(json.dumps(FIXTURE))
            malformed["review_package_ref"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(JobValidationError, "REVIEW_PACKAGE_REF_INVALID"):
                self.parse(malformed)

    def test_actor_mode_workspace_full_review_and_callback_rules_unchanged(self):
        for key, value, error in (
            ("actor", "codex", "Actor/mode not allowed"),
            ("mode", "implementation", "Actor/mode not allowed"),
            ("workspace", "unknown", "Unknown workspace"),
        ):
            data = dict(FIXTURE)
            data[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(JobValidationError, error):
                self.parse(data)
        full = dict(FIXTURE)
        full["review_mode"] = "FULL_REVIEW"
        full.pop("review_package_ref")
        job = self.parse(full)
        self.assertEqual(job.review_mode, "FULL_REVIEW")
        self.assertFalse(job.measurement_mode)
        self.assertEqual(job.callback_url, FIXTURE["callback"]["url"])


if __name__ == "__main__":
    unittest.main()
