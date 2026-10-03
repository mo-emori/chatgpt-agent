import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import context_harness
import evidence_index
import job_context
import agent_worker


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(job_context.canonical(value))


class JobContextTests(unittest.TestCase):
    def setUp(self):
        Path(".tmp-tests").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=".tmp-tests")
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / ".agent").mkdir()
        (self.root / "bootstrap-contract.md").write_text("contract\n", encoding="utf-8")
        (self.root / "bootstrap-adr.md").write_text("adr\n", encoding="utf-8")
        (self.root / "registry.json").write_text("{}\n", encoding="utf-8")
        (self.root / "unrelated.txt").write_text("other\n", encoding="utf-8")
        self.declaration = {
            "schema_version": 1, "mode": "SHADOW", "generated_root": "validation/context",
            "capabilities": {"CAP": {
                "selectors": {"actors": ["codex"], "modes": ["execute"]},
                "protected_paths": ["security"], "forbidden_paths": ["secrets"],
                "sources": [
                    {"path": "bootstrap-contract.md", "kind": "contract", "authority": "authoritative",
                     "context_items": ["bootstrap"], "required": True},
                    {"path": "bootstrap-adr.md", "kind": "adr", "authority": "authoritative",
                     "context_items": ["bootstrap"], "required": True},
                    {"path": "registry.json", "kind": "registry", "authority": "observed",
                     "context_items": ["runtime"], "required": True},
                    {"path": "unrelated.txt", "kind": "notes", "authority": "observed",
                     "context_items": ["unrelated"], "required": True},
                ]}}}
        write_json(self.root / ".agent/context.json", self.declaration)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)
        self.manifest = context_harness.observe(self.root, "CAP")
        self.delta = context_harness.scan(self.manifest, self.manifest)
        self.dest = self.root / "validation/context/CAP"
        write_json(self.dest / "context-manifest.json", self.manifest)
        write_json(self.dest / "delta-report.json", self.delta)
        self.evidence_path = self.root / "evidence/result.json"
        self.evidence = {"job_id": "REVIEW-1", "actor": "claude", "mode": "review",
                         "workspace": "ws", "capability": "CAP", "status": "DONE",
                         "review_execution": {"actor_status": "DONE"},
                         "review_verdict": "CHANGES_REQUESTED",
                         "findings": [{"finding_id": "F-1", "status": "OPEN"}]}
        write_json(self.evidence_path, self.evidence)
        self.index, _ = evidence_index.build_index(self.root, workspace="ws", capability="CAP",
            sources=[{"path": "evidence/result.json", "kind": "review", "required": True}])
        write_json(self.dest / "evidence-index.json", self.index)

    def tearDown(self):
        self.temp.cleanup()

    def job(self, request=None):
        ref = {"type": "notion_page", "page_id": "p"}
        if request is not None:
            ref["context_request"] = request
        return SimpleNamespace(protocol_version="3", workspace="ws",
                               job_id="JOB", actor="codex", mode="execute",
                               prompt_sha256="a" * 64, instruction_ref=ref, prompt="DO NOT CHANGE")

    def build(self, job=None, delta=None, manifest=None, index=None):
        manifest, delta, index = manifest or self.manifest, delta or self.delta, index or self.index
        write_json(self.dest / "context-manifest.json", manifest)
        write_json(self.dest / "delta-report.json", delta)
        write_json(self.dest / "evidence-index.json", index)
        return job_context.build(self.root, workspace="ws", capability="CAP", job=job or self.job(),
            manifest=manifest, manifest_path="validation/context/CAP/context-manifest.json",
            delta=delta, delta_path="validation/context/CAP/delta-report.json",
            evidence_index=index, evidence_index_path="validation/context/CAP/evidence-index.json",
            declaration=self.declaration)

    def test_deterministic_order_no_impact_and_explicit_dependency(self):
        job = self.job({"context_items": ["runtime"], "target_files": [], "finding_ids": ["F-1"]})
        one, report1 = self.build(job); two, report2 = self.build(job)
        self.assertEqual((one, report1["sha256"]), (two, report2["sha256"]))
        self.assertEqual(one["delta_status"], "NO_IMPACT")
        self.assertEqual([x["source_ref"] for x in one["authoritative_source_refs"]],
                         ["bootstrap-adr.md", "bootstrap-contract.md", "registry.json"])
        self.assertEqual(one["omitted_sources"],
                         [{"source_ref": "unrelated.txt", "reason": "EXPLICITLY_UNRELATED_DEPENDENCY"}])
        self.assertEqual(one["requested_finding_ids"], ["F-1"])

    def test_delta_states_and_approved_semantics_are_not_mutated(self):
        original = json.loads(json.dumps(self.manifest["approved_semantics"]))
        for state, expected in (("CONTEXT_UPDATE", "READY_BOUNDED"),
                                ("POTENTIAL_AUTHORITY_CHANGE", "NEEDS_RECONCILIATION"),
                                ("UNVERIFIABLE", "UNVERIFIABLE")):
            delta = dict(self.delta); delta["delta_status"] = state
            if state == "POTENTIAL_AUTHORITY_CHANGE":
                delta["changed_sources"] = [{"source": "bootstrap-contract.md", "authority": "authoritative"}]
            result, _ = self.build(delta=delta)
            self.assertEqual(result["selection_status"], expected)
        self.assertEqual(self.manifest["approved_semantics"], original)

    def test_partial_unstructured_and_failed_429_never_fabricate(self):
        entry = dict(self.index["entries"][0])
        entry.update({"quality": "UNSTRUCTURED", "findings": None, "job_status": "FAILED",
                      "failure_class": "SESSION_LIMIT", "actor_execution_status": "AGENT_ERROR",
                      "review_verdict": None})
        index = dict(self.index); index["entries"] = [entry]
        result, _ = self.build(job=self.job({"finding_ids": ["F-6"]}), index=index)
        self.assertEqual(result["evidence_refs"][0]["review_verdict"], None)
        self.assertEqual(result["evidence_refs"][0]["findings"], None)
        self.assertEqual(result["selection_status"], "NEEDS_RECONCILIATION")
        self.assertIn("F-6", [x.get("finding_id") for x in result["unresolved_items"]])
        self.assertTrue(result["expansion_requirements"])

    def test_boundaries_and_budget_never_truncate_authority(self):
        result, _ = self.build(job=self.job({"max_text_bytes": 0}))
        self.assertEqual(result["boundaries"], {"protected_paths": ["security"],
                                                "forbidden_paths": ["secrets"]})
        self.assertTrue(any(x["source_ref"] == "bootstrap-contract.md"
                            for x in result["authoritative_source_refs"]))
        self.assertTrue(any(x["reason"] == "SIZE_BUDGET_CANNOT_TRUNCATE_REQUIRED_AUTHORITY"
                            for x in result["expansion_requirements"]))

    def test_hash_mismatch_traversal_stale_and_symlink_escape(self):
        (self.dest / "delta-report.json").write_text("{}", encoding="utf-8")
        result, _ = job_context.build(self.root, workspace="ws", capability="CAP", job=self.job(),
            manifest=self.manifest, manifest_path="validation/context/CAP/context-manifest.json",
            delta=self.delta, delta_path="validation/context/CAP/delta-report.json",
            evidence_index=self.index, evidence_index_path="validation/context/CAP/evidence-index.json",
            declaration=self.declaration)
        self.assertEqual(result["selection_status"], "UNVERIFIABLE")
        with self.assertRaises(ValueError): job_context.safe_file(self.root, "../escape")
        outside = self.root.parent / (self.root.name + "-outside")
        outside.mkdir(exist_ok=True)
        try:
            (self.root / "link").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(ValueError): job_context.safe_file(self.root, "link/file")

    def test_generate_diagnostics_and_prompt_unchanged(self):
        job = self.job(); prompt = job.prompt
        report = job_context.generate(self.root, workspace="ws", capability="CAP", job=job,
            manifest=self.manifest, manifest_path="validation/context/CAP/context-manifest.json",
            delta=self.delta, delta_path="validation/context/CAP/delta-report.json",
            evidence_index=self.index, evidence_index_path="validation/context/CAP/evidence-index.json",
            declaration=self.declaration)
        self.assertEqual(job.prompt, prompt)
        self.assertEqual(report["mode"], "COMPARISON_ONLY")
        self.assertTrue((self.root / report["report_path"]).is_file())
        self.assertIn("source_context_sha256", report)
        response = agent_worker.build_result(job, status="DONE", runtime={},
            context={"job_context": report, "evidence_index": {"status": "READY"}})
        self.assertEqual(response["job_context"], report)
        self.assertEqual(response["status"], "DONE")

    def test_argus_six_findings_only_when_structured(self):
        findings = [{"finding_id": f"F-{n}", "status": "OPEN"} for n in range(1, 7)]
        evidence = dict(self.evidence); evidence["findings"] = findings
        write_json(self.evidence_path, evidence)
        index, _ = evidence_index.build_index(self.root, workspace="ws", capability="CAP",
            sources=[{"path": "evidence/result.json", "kind": "review", "required": True}])
        result, _ = self.build(job=self.job({"finding_ids": [f"F-{n}" for n in range(1, 7)]}), index=index)
        self.assertEqual(len(result["evidence_refs"][0]["findings"]), 6)
        self.assertFalse(any(x["code"].startswith("REQUESTED_FINDING") for x in result["unresolved_items"]))


if __name__ == "__main__":
    unittest.main()
