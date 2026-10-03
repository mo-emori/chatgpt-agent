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
                "context": {"max_text_bytes": 100000},
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

    def conditional_sources(self, sources):
        self.declaration["capabilities"]["CAP"]["sources"] = sources
        write_json(self.root / ".agent/context.json", self.declaration)
        self.manifest = context_harness.observe(self.root, "CAP")
        self.delta = context_harness.scan(self.manifest, self.manifest)

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
        self.assertEqual(result["optional_evidence_candidates"][0]["review_verdict"], None)
        self.assertEqual(result["optional_evidence_candidates"][0]["findings"], None)
        self.assertEqual(result["selection_status"], "NEEDS_RECONCILIATION")
        self.assertIn("F-6", [x.get("finding_id") for x in result["unresolved_items"]])
        self.assertTrue(result["expansion_requirements"])

    def test_unstructured_without_edge_is_optional_not_blocking(self):
        entry = dict(self.index["entries"][0])
        entry.update({"quality": "UNSTRUCTURED", "findings": None, "capability": "CAP"})
        index = dict(self.index); index["entries"] = [entry]
        result, report = self.build(index=index)
        self.assertEqual(result["selection_status"], "READY_BOUNDED")
        self.assertEqual(result["optional_evidence_candidates"][0]["relevance"], "BOUNDED_CANDIDATE")
        self.assertEqual(report["required_expansion_count"], 0)
        self.assertEqual(report["optional_candidate_count"], 1)

    def test_unstructured_explicit_review_of_target_is_required(self):
        entry = dict(self.index["entries"][0])
        entry.update({"quality": "UNSTRUCTURED", "findings": None, "review_of": "JOB"})
        index = dict(self.index); index["entries"] = [entry]
        result, report = self.build(index=index)
        self.assertEqual(result["evidence_refs"][0]["relevance"], "REQUIRED_RELEVANT")
        self.assertEqual(result["selection_status"], "NEEDS_RECONCILIATION")
        self.assertEqual(report["required_expansion_count"], 1)

    def test_explicit_capability_mismatch_is_irrelevant(self):
        entry = dict(self.index["entries"][0]); entry["capability"] = "OTHER"
        index = dict(self.index); index["entries"] = [entry]
        result, report = self.build(index=index)
        self.assertEqual(result["omitted_irrelevant_evidence"][0]["reason"],
                         "EXPLICIT_CAPABILITY_MISMATCH")
        self.assertEqual(report["omitted_irrelevant_count"], 1)

    def test_budget_never_changes_authority_selection(self):
        result, _ = self.build(job=self.job({"max_text_bytes": 0}))
        self.assertEqual(result["boundaries"], {"protected_paths": ["security"],
                                                "forbidden_paths": ["secrets"]})
        self.assertTrue(any(x["source_ref"] == "bootstrap-contract.md"
                            for x in result["authoritative_source_refs"]))
        self.assertEqual(result["selection_status"], "READY_BOUNDED")
        self.assertEqual(result["budget_contract"]["reason"], "INVALID_BUDGET_OVERRIDE")

    def test_budget_precedence_and_identity(self):
        inherited, _ = self.build(self.job({}))
        lower, _ = self.build(self.job({"max_text_bytes": 50000}))
        equal, _ = self.build(self.job({"max_text_bytes": 100000}))
        higher, _ = self.build(self.job({"max_text_bytes": 100001}))
        self.assertEqual(inherited["budget_contract"]["effective_max_text_bytes"], 100000)
        self.assertEqual(inherited["budget_contract"]["budget_source"], "CAPABILITY_DECLARATION")
        self.assertEqual(lower["budget_contract"]["effective_max_text_bytes"], 50000)
        self.assertEqual(lower["budget_contract"]["budget_source"], "JOB_CONTEXT_REQUEST")
        self.assertEqual(equal["budget_contract"]["validation_status"], "VALID")
        self.assertEqual(higher["budget_contract"]["reason"],
                         "BUDGET_OVERRIDE_EXCEEDS_CAPABILITY_MAX")
        self.assertIsNone(higher["budget_contract"]["effective_max_text_bytes"])
        self.assertNotEqual(inherited["package_sha256"], lower["package_sha256"])

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

    def test_legacy_and_explicit_base_authority(self):
        self.declaration["capabilities"]["CAP"]["sources"][1]["always_required"] = True
        self.conditional_sources(self.declaration["capabilities"]["CAP"]["sources"])
        result, _ = self.build()
        refs = {x["source_ref"]: x for x in result["authoritative_source_refs"]}
        self.assertEqual(set(refs), {"bootstrap-contract.md", "bootstrap-adr.md"})
        self.assertIn("BASE_AUTHORITY", refs["bootstrap-contract.md"]["reason_codes"])
        self.assertIn("BASE_AUTHORITY", refs["bootstrap-adr.md"]["reason_codes"])

    def test_conditional_target_match_and_unrelated_omission(self):
        sources = self.declaration["capabilities"]["CAP"]["sources"][:2]
        sources[0].update(always_required=False, target_files=["src/bootstrap.py"])
        sources[1].update(always_required=False, target_files=["src/other.py"])
        self.conditional_sources(sources)
        result, _ = self.build(self.job({"target_files": ["src/bootstrap.py"]}))
        self.assertEqual([x["source_ref"] for x in result["authoritative_source_refs"]],
                         ["bootstrap-contract.md"])
        self.assertEqual(result["authoritative_source_refs"][0]["reason"], "TARGET_MATCH")
        self.assertEqual(result["omitted_sources"][0]["reason"], "PROVEN_UNRELATED_CONDITIONAL")

    def test_conditional_context_item_dependency_closure_and_cycle(self):
        sources = self.declaration["capabilities"]["CAP"]["sources"][:2]
        sources[0].update(always_required=False, context_items=["bootstrap"],
                          depends_on=["bootstrap-adr.md"])
        sources[1].update(always_required=False, context_items=["adr"],
                          depends_on=["bootstrap-contract.md"])
        self.conditional_sources(sources)
        first, _ = self.build(self.job({"context_items": ["bootstrap"]}))
        second, _ = self.build(self.job({"context_items": ["bootstrap"]}))
        self.assertEqual(first["package_sha256"], second["package_sha256"])
        refs = {x["source_ref"]: x for x in first["authoritative_source_refs"]}
        self.assertIn("CONTEXT_ITEM_MATCH", refs["bootstrap-contract.md"]["reason_codes"])
        self.assertIn("DEPENDENCY_CLOSURE", refs["bootstrap-adr.md"]["reason_codes"])

    def test_conditional_empty_request_fails_closed(self):
        source = self.declaration["capabilities"]["CAP"]["sources"][0]
        source.update(always_required=False, target_files=["src/bootstrap.py"])
        self.conditional_sources([source])
        result, _ = self.build(self.job({}))
        self.assertEqual(result["selection_status"], "NEEDS_RECONCILIATION")
        self.assertEqual([x["source_ref"] for x in result["authoritative_source_refs"]],
                         ["bootstrap-contract.md"])
        self.assertTrue(any(x["code"] == "MISSING_STRUCTURED_REQUEST"
                            for x in result["reconciliation_requirements"]))

    def test_conditional_ambiguous_mapping_fails_closed(self):
        source = self.declaration["capabilities"]["CAP"]["sources"][0]
        source.update(always_required=False, target_files=["src/bootstrap.py"])
        self.conditional_sources([source])
        result, _ = self.build(self.job({"target_files": ["src/unmapped.py"]}))
        self.assertEqual(result["selection_status"], "NEEDS_RECONCILIATION")
        self.assertIn("AMBIGUOUS_SELECTOR_MAPPING",
                      result["authoritative_source_refs"][0]["reason_codes"])

    def test_changed_conditional_authority_is_required(self):
        sources = self.declaration["capabilities"]["CAP"]["sources"][:2]
        sources[0].update(always_required=False, target_files=["src/bootstrap.py"])
        sources[1].update(always_required=False, target_files=["src/other.py"])
        self.conditional_sources(sources)
        delta = dict(self.delta)
        delta.update(delta_status="POTENTIAL_AUTHORITY_CHANGE", changed_sources=[{
            "source": "bootstrap-contract.md", "authority": "authoritative"}])
        result, _ = self.build(self.job({"target_files": ["src/other.py"]}), delta=delta)
        refs = {x["source_ref"]: x for x in result["authoritative_source_refs"]}
        self.assertIn("CHANGED_AUTHORITY", refs["bootstrap-contract.md"]["reason_codes"])
        self.assertIn("TARGET_MATCH", refs["bootstrap-adr.md"]["reason_codes"])

    def test_changed_non_authority_evidence_is_not_authority_and_keeps_evidence_semantics(self):
        self.declaration["capabilities"]["CAP"]["sources"].append({
            "path": "evidence/result.json", "kind": "evidence", "authority": "non_authority",
            "context_items": ["review"], "required": True})
        self.conditional_sources(self.declaration["capabilities"]["CAP"]["sources"])
        delta = dict(self.delta)
        delta.update(delta_status="CONTEXT_UPDATE", changed_sources=[{
            "source": "evidence/result.json", "authority": "non_authority",
            "change_kind": "STATUS_CHANGED"}])
        result, report = self.build(self.job({"finding_ids": ["F-1"]}), delta=delta)
        self.assertNotIn("evidence/result.json",
                         {x["source_ref"] for x in result["authoritative_source_refs"]})
        self.assertEqual(report["authority_ref_count"], 2)
        self.assertEqual(result["evidence_refs"][0]["relevance"], "REQUIRED_RELEVANT")
        self.assertEqual(result["evidence_refs"][0]["trust"], self.index["entries"][0]["trust"])
        self.assertEqual(result["evidence_refs"][0]["quality"], self.index["entries"][0]["quality"])
        self.assertEqual(result["changed_sources"][0]["source"], "evidence/result.json")

    def test_mixed_four_authority_and_one_evidence_change_counts_only_authority(self):
        (self.root / "authority-three.md").write_text("three\n", encoding="utf-8")
        (self.root / "authority-four.md").write_text("four\n", encoding="utf-8")
        self.declaration["capabilities"]["CAP"]["sources"].extend([
            {"path": "authority-three.md", "kind": "contract", "authority": "authoritative"},
            {"path": "authority-four.md", "kind": "adr", "authority": "authoritative"},
            {"path": "evidence/result.json", "kind": "evidence", "authority": "non_authority"},
        ])
        self.conditional_sources(self.declaration["capabilities"]["CAP"]["sources"])
        changed = [{"source": path, "authority": authority, "change_kind": "STATUS_CHANGED"}
                   for path, authority in (
                       ("bootstrap-contract.md", "authoritative"),
                       ("bootstrap-adr.md", "authoritative"),
                       ("authority-three.md", "authoritative"),
                       ("authority-four.md", "authoritative"),
                       ("evidence/result.json", "non_authority"))]
        delta = dict(self.delta)
        delta.update(delta_status="POTENTIAL_AUTHORITY_CHANGE", changed_sources=changed)
        result, report = self.build(self.job({"finding_ids": ["F-1"]}), delta=delta)
        self.assertEqual(report["authority_ref_count"], 4)
        self.assertEqual({x["source_ref"] for x in result["authoritative_source_refs"]}, {
            "bootstrap-contract.md", "bootstrap-adr.md", "authority-three.md", "authority-four.md"})
        self.assertTrue(all("CHANGED_AUTHORITY" in x["reason_codes"]
                            for x in result["authoritative_source_refs"]))
        self.assertEqual(result["selection_status"], "NEEDS_RECONCILIATION")
        self.assertTrue(any(x["code"] == "CHANGED_AUTHORITY_REQUIRES_DECISION"
                            for x in result["reconciliation_requirements"]))

    def test_invalid_declaration_dependency_reference_rejected(self):
        source = self.declaration["capabilities"]["CAP"]["sources"][0]
        source["depends_on"] = ["missing.md"]
        write_json(self.root / ".agent/context.json", self.declaration)
        declaration, _, errors = context_harness.load_declaration(self.root)
        self.assertIsNone(declaration)
        self.assertIn("unknown source dependency", errors[0])

    def test_selector_semantics_change_job_context_hash(self):
        source = self.declaration["capabilities"]["CAP"]["sources"][0]
        source.update(always_required=False, target_files=["src/bootstrap.py"])
        self.conditional_sources([source])
        one, _ = self.build(self.job({"target_files": ["src/bootstrap.py"]}))
        self.declaration["capabilities"]["CAP"]["sources"][0]["target_files"] = ["src/renamed.py"]
        # Keep the same observed inputs to prove selector semantics themselves are identity material.
        two, _ = self.build(self.job({"target_files": ["src/bootstrap.py"]}))
        self.assertNotEqual(one["package_sha256"], two["package_sha256"])

    def test_lower_trust_evidence_does_not_replace_authority(self):
        result, _ = self.build(self.job({"finding_ids": ["F-1"]}))
        self.assertEqual(len([x for x in result["authoritative_source_refs"]
                              if x["authority"] == "authoritative"]), 2)
        self.assertEqual(result["evidence_refs"][0]["relevance"], "REQUIRED_RELEVANT")

    def test_argus_like_legacy_fixture_still_selects_four_authorities(self):
        template = json.loads((Path(__file__).parents[1] /
            "docs/argus_runtime_bootstrap_context_phase1.json").read_text("utf-8"))
        specs = template["capabilities"]["RUNTIME-BOOTSTRAP-ORCHESTRATOR"]["sources"]
        self.assertEqual(sum(s.get("authority", "authoritative") == "authoritative" and
                             s.get("always_required", True) for s in specs), 4)


if __name__ == "__main__":
    unittest.main()
