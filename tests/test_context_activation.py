import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from artifacts.manifest import append_manifest_instruction
from context_activation import CLOSE, OPEN, compose, evaluate_gate, prepare
from actors import codex
from actors.process_runner import ProcessResult
import agent_worker


class ContextActivationTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(".tmp-tests")
        temp_root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=temp_root)
        self.root = Path(self.tmp.name)
        self.instruction = "日本語\r\nkeep CRLF"
        self.job = SimpleNamespace(
            prompt=self.instruction,
            prompt_sha256=hashlib.sha256(self.instruction.encode("utf-8")).hexdigest(),
            instruction_ref={"context_request": {"phase": "implementation"}},
            workspace="test", job_id="j", actor="codex", mode="implementation")
        self.context = self._context(
            b'{"raw_provenance_payload_bytes":0,"reference":"' + OPEN.encode() + b'"}\n')

    def tearDown(self):
        self.tmp.cleanup()

    def _context(self, payload):
        body = json.loads(payload)
        canonical = (json.dumps(body, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")) + "\n").encode()
        identity = hashlib.sha256(canonical).hexdigest()
        value = dict(body, materialized_context_sha256=identity)
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) + "\n").encode()
        path = self.root / "materialized.json"
        path.write_bytes(raw)
        report = {"status": "READY_BOUNDED", "sha256": "r" * 64,
                  "materialized_context_sha256": identity,
                  "materialized_context_path": path.name,
                  "budget_status": "VALID", "required_payload_bytes": len(raw),
                  "effective_max_text_bytes": len(raw), "raw_provenance_payload_bytes": 0,
                  "projection_freshness": [{"freshness_status": "CURRENT"}],
                  "projection_update_required": {"context_items": [], "target_files": []},
                  "diagnostics": []}
        return {"delta_status": "NO_IMPACT", "would_block": False,
                "manifest_sha256": "m" * 64, "manifest_path": "manifest.json",
                "report_path": "delta.json",
                "job_context": {"status": "READY_BOUNDED", "sha256": "j" * 64,
                                "job_context_path": "job.json",
                                "expansion_required_count": 0,
                                "required_expansion_count": 0},
                "materialized_context": report}

    def test_no_request_default_is_exact_legacy(self):
        self.job.instruction_ref = {}
        result = prepare(self.job, "OFF", None, self.root)
        expected = append_manifest_instruction(self.instruction).encode()
        self.assertEqual(result["actor_input"], expected)
        self.assertEqual(result["context_activation_status"], "LEGACY")

    def test_off_request_is_exact_legacy(self):
        result = prepare(self.job, "OFF", None, self.root)
        self.assertEqual(result["actor_input"], append_manifest_instruction(self.instruction).encode())
        self.assertEqual(result["context_activation_status"], "OFF")

    def test_shadow_ready_previews_but_keeps_legacy(self):
        result = prepare(self.job, "SHADOW", self.context, self.root)
        self.assertEqual(result["context_activation_status"], "SHADOW_PREVIEW")
        self.assertEqual(result["actor_input"], append_manifest_instruction(self.instruction).encode())
        self.assertIsNotNone(result["effective_input_sha256"])
        self.assertNotEqual(result["actor_input_sha256"], result["effective_input_sha256"])

    def test_shadow_failure_is_diagnostic(self):
        self.context["delta_status"] = "POTENTIAL_AUTHORITY_CHANGE"
        result = prepare(self.job, "SHADOW", self.context, self.root)
        self.assertIn("DELTA_NOT_SAFE", result["gate_reason_codes"])
        self.assertEqual(result["context_activation_status"], "SHADOW_PREVIEW")

    def test_enforce_ready_injects_deterministically_and_preserves_instruction(self):
        one = prepare(self.job, "ENFORCE_AND_INJECT", self.context, self.root)
        two = prepare(self.job, "ENFORCE_AND_INJECT", self.context, self.root)
        self.assertEqual(one["actor_input"], two["actor_input"])
        self.assertTrue(one["actor_input"].startswith(self.instruction.encode() + b"\n"))
        self.assertIn(b"context_payload_bytes=", one["actor_input"])
        self.assertIn(CLOSE.encode(), one["actor_input"])
        self.assertEqual(hashlib.sha256(one["actor_input"]).hexdigest(),
                         one["effective_input_sha256"])
        self.assertEqual(one["actor_input_sha256"], one["effective_input_sha256"])
        self.assertEqual(self.job.prompt_sha256,
                         hashlib.sha256(self.instruction.encode()).hexdigest())

    def test_gate_projection_budget_expansion_and_raw_provenance(self):
        cases = [
            ("PROJECTION_NOT_CURRENT", lambda c: c["materialized_context"].update(
                projection_freshness=[{"freshness_status": "STALE"}])),
            ("PROJECTION_UPDATE_REQUIRED", lambda c: c["materialized_context"].update(
                projection_update_required={"context_items": ["x"], "target_files": []})),
            ("REQUIRED_PAYLOAD_OVER_BUDGET", lambda c: c["materialized_context"].update(
                required_payload_bytes=11, effective_max_text_bytes=10)),
            ("EXPANSION_REQUIRED", lambda c: c["job_context"].update(
                expansion_required_count=1)),
            ("RAW_PROVENANCE_PAYLOAD", lambda c: c["materialized_context"].update(
                raw_provenance_payload_bytes=1)),
        ]
        for code, mutate in cases:
            context = json.loads(json.dumps(self.context))
            mutate(context)
            reasons, _ = evaluate_gate(context, self.root)
            self.assertIn(code, reasons)
            self.assertEqual(prepare(self.job, "ENFORCE_AND_INJECT", context, self.root)[
                "context_activation_status"], "BLOCKED")

    def test_codex_subprocess_receives_exact_injected_text(self):
        activation = prepare(self.job, "ENFORCE_AND_INJECT", self.context, self.root)
        captured = {}
        def fake(**kwargs):
            captured.update(kwargs)
            return ProcessResult(0, "", "")
        with patch.object(codex, "WORKSPACES", {"test": {
                "path": self.root, "allow_skip_git_repo_check": True}}), \
             patch.object(codex, "validate_workspace"), \
             patch.object(codex, "run_process", side_effect=fake):
            codex.run(self.job, prompt=activation["actor_input"].decode(), precomposed=True)
        self.assertEqual(captured["input_text"].encode(), activation["actor_input"])

    def test_worker_block_does_not_call_actor_and_callback_still_runs(self):
        blocked = json.loads(json.dumps(self.context))
        blocked["materialized_context"]["projection_freshness"] = [
            {"freshness_status": "STALE"}]
        self.job.protocol_version = "3"
        self.job.callback_type = "chatgpt_browser"
        self.job.callback_url = "https://example.invalid"
        before = {"head": "h"}
        with patch.object(agent_worker, "CONTEXT_HARNESS_ACTIVATION_MODE",
                          "ENFORCE_AND_INJECT"), \
             patch.object(agent_worker, "prepare_execution",
                          return_value=({}, self.root, self.root, before)), \
             patch.object(agent_worker, "begin_shadow", return_value={}), \
             patch.object(agent_worker, "finish_shadow", return_value=blocked), \
             patch.object(agent_worker, "run_agent") as run_actor, \
             patch.object(agent_worker.state_store, "mark_completed"), \
             patch.object(agent_worker, "publish_slack_result") as publish, \
             patch.object(agent_worker, "finalize_browser_callback") as callback, \
             patch.object(agent_worker, "log_job_end"), \
             patch.object(agent_worker, "dispatch_next_queued"):
            agent_worker.execute_job(self.job, lambda **kwargs: None)
        run_actor.assert_not_called()
        self.assertEqual(publish.call_args.args[2]["status"], "BLOCKED_CONTEXT")
        self.assertFalse(publish.call_args.args[2]["actor_started"])
        callback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
