import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import historical_job_evidence as evidence


class HistoricalJobEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.canonical = self.base / "canonical"
        self.logs = self.base / "logs" / "JOB-1"
        self.canonical.mkdir()
        self.logs.mkdir(parents=True)
        (self.canonical / "dirty.txt").write_text("untouched", encoding="utf-8")
        self.message = "BLOCKED\r\n\r\nExact reason.\r\n"
        (self.logs / "stdout.txt").write_bytes(self.message.encode())
        (self.logs / "stderr.txt").write_text("tool trace SECRET\n", encoding="utf-8")
        self.request = {
            "protocol_version": "3", "job_id": "JOB-1", "actor": "codex",
            "mode": "implementation", "workspace": "argus",
            "prompt_sha256": "a" * 64, "instruction_sha256": "a" * 64,
            "instruction_ref": {"type": "notion_page", "page_id": "page"},
            "callback": {"url": "https://secret.invalid"},
        }
        self.result = {
            "protocol_version": "3", "job_id": "JOB-1", "actor": "codex",
            "mode": "implementation", "workspace": "argus",
            "prompt_sha256": "a" * 64, "instruction_sha256": "a" * 64,
            "instruction_ref": self.request["instruction_ref"], "status": "DONE",
            "exit_code": 0, "summary": "actor summary", "artifact_status": "DONE",
            "artifacts": [], "runtime": {"actor": "codex"},
            "git": {"baseline_commit": "b" * 40, "head_after": "b" * 40,
                    "changed_paths": ["x"]},
            "callback": {"url": "https://secret.invalid"},
        }
        self.state = {
            "job_id": "JOB-1", "actor": "codex", "mode": "implementation",
            "workspace": "argus", "prompt_sha256": "a" * 64,
            "instruction_ref": json.dumps(self.request["instruction_ref"]),
            "status": "DONE", "exit_code": 0, "failure_class": None,
            "received_at": "2026-10-02T00:00:00+00:00",
        }
        self.slack = {
            "job_id": "JOB-1", "actor": "codex", "mode": "implementation",
            "workspace": "argus", "instruction_sha256": "a" * 64,
            "status": "DONE", "exit_code": 0,
            "git": {"baseline_commit": "b" * 40, "head_after": "b" * 40},
        }
        self._write_inputs()

    def tearDown(self):
        self.temp.cleanup()

    def _write_inputs(self):
        (self.logs / "request.json").write_text(json.dumps(self.request), encoding="utf-8")
        (self.logs / "result.json").write_text(json.dumps(self.result), encoding="utf-8")
        self.slack_path = self.base / "slack-result.json"
        self.slack_path.write_text(json.dumps(self.slack), encoding="utf-8")

    def adopt(self, **overrides):
        values = dict(canonical=self.canonical,
                      evidence_root="validation/evidence/job-results",
                      job_id="JOB-1", workspace="argus", log_dir=self.logs,
                      state_row=self.state, slack_manifest_path=self.slack_path,
                      human_approved=True)
        values.update(overrides)
        return evidence.adopt_historical_job_evidence(**values)

    def destination(self):
        return self.canonical / "validation/evidence/job-results/JOB-1"

    def test_historical_stdout_extraction_is_exact_except_documented_newlines(self):
        actual = evidence.extract_final_actor_message(self.message.encode())
        self.assertEqual(actual, "BLOCKED\n\nExact reason.\n")
        for raw in (b"", b" \r\n", b"answer\x00tail", b"\xff", b"answer\x1b[31m"):
            with self.subTest(raw=raw):
                with self.assertRaises(evidence.HistoricalEvidenceError):
                    evidence.extract_final_actor_message(raw)

    def test_package_separates_authority_and_never_copies_raw_logs(self):
        result = self.adopt()
        self.assertEqual(result["status"], "ADOPTED")
        destination = self.destination()
        self.assertFalse((destination / "stdout.txt").exists())
        self.assertFalse((destination / "stderr.txt").exists())
        normalized = json.loads((destination / "normalized-result.json").read_text("utf-8"))
        actor = json.loads((destination / "actor-reported.json").read_text("utf-8"))
        manifest = json.loads((destination / "job-evidence-manifest.json").read_text("utf-8"))
        self.assertNotIn("summary", normalized["worker_observed"])
        self.assertNotIn("callback", normalized["worker_observed"])
        self.assertEqual(normalized["actor_reported"], {"summary": "actor summary"})
        self.assertEqual(actor["final_actor_message"], "BLOCKED\n\nExact reason.\n")
        self.assertEqual(actor["final_actor_message_sha256"], hashlib.sha256(
            actor["final_actor_message"].encode()).hexdigest())
        self.assertEqual({x["path"] for x in manifest["raw_local_only"]},
                         {"stdout.txt", "stderr.txt"})
        self.assertTrue(all(x["storage"] == "LOCAL_ONLY"
                            for x in manifest["raw_local_only"]))
        self.assertIn("not a standalone safety gate", manifest["trust_limitation"])

    def test_explicit_human_approval_is_required(self):
        result = self.adopt(human_approved=False)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("human-approved", result["error"])
        self.assertFalse(self.destination().exists())

    def test_state_store_and_slack_match_or_fail_closed(self):
        self.assertEqual(self.adopt()["corroboration"]["state_store"]["status"], "MATCHED")
        # A fresh destination ensures failure is provenance-driven, not collision-driven.
        self.temp.cleanup(); self.setUp()
        bad_state = dict(self.state, actor="claude")
        self.assertEqual(self.adopt(state_row=bad_state)["status"], "FAILED")
        self.assertFalse(self.destination().exists())
        self.slack["exit_code"] = 1
        self._write_inputs()
        result = self.adopt()
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("exit_code", result["error"])

    def test_manifest_is_deterministic_repeat_is_noop_and_dirty_file_untouched(self):
        before = (self.canonical / "dirty.txt").read_bytes()
        first = self.adopt()
        manifest = (self.destination() / "job-evidence-manifest.json").read_bytes()
        second = self.adopt()
        self.assertEqual((first["status"], second["status"]), ("ADOPTED", "NOOP"))
        self.assertEqual(first["manifest_sha256"], hashlib.sha256(manifest).hexdigest())
        self.assertEqual(second["manifest_sha256"], first["manifest_sha256"])
        self.assertEqual((self.canonical / "dirty.txt").read_bytes(), before)

    def test_different_collision_fails_without_overwrite(self):
        self.assertEqual(self.adopt()["status"], "ADOPTED")
        original = (self.destination() / "actor-reported.json").read_bytes()
        (self.logs / "stdout.txt").write_text("different", encoding="utf-8")
        self.assertEqual(self.adopt()["status"], "FAILED")
        self.assertEqual((self.destination() / "actor-reported.json").read_bytes(), original)

    def test_containment_and_symlink_protection(self):
        for root in ("../escape", "C:\\escape", "safe:stream", "CON/path"):
            with self.subTest(root=root):
                self.assertEqual(self.adopt(evidence_root=root)["status"], "FAILED")
        self.assertEqual(self.adopt(job_id="../JOB")["status"], "FAILED")
        outside = self.base / "outside"; outside.mkdir()
        link = self.canonical / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            return
        self.assertEqual(self.adopt(evidence_root="linked/results")["status"], "FAILED")

    def test_atomic_staging_uses_single_replace(self):
        real_replace = os.replace
        calls = []
        def observed(source, destination):
            calls.append((Path(source), Path(destination)))
            self.assertEqual(Path(source).parent, Path(destination).parent)
            return real_replace(source, destination)
        with patch.object(evidence.os, "replace", side_effect=observed):
            self.assertEqual(self.adopt()["status"], "ADOPTED")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
