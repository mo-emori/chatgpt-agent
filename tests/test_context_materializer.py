import copy
import json
import os

import context_materializer
import job_context
from tests.test_job_context import JobContextTests, write_json


class ContextMaterializerTests(JobContextTests):
    def materialize(self, request=None, *, index=None):
        context, _ = self.build(job=self.job(request or {"max_text_bytes": 100000,
            "finding_ids": ["F-1"]}), index=index)
        write_json(self.dest / "job-context.json", context)
        value, report = context_materializer.build(
            self.root, workspace="ws", capability="CAP", job_context=context,
            job_context_path="validation/context/CAP/job-context.json",
            evidence_index=index or self.index,
            evidence_index_path="validation/context/CAP/evidence-index.json")
        return context, value, report

    def test_exact_authority_and_structured_evidence_with_hashes(self):
        job = self.job({"max_text_bytes": 100000, "finding_ids": ["F-1"]})
        original_prompt, original_sha = job.prompt, job.prompt_sha256
        context, _ = self.build(job=job)
        write_json(self.dest / "job-context.json", context)
        value, _ = context_materializer.build(self.root, workspace="ws", capability="CAP",
            job_context=context, job_context_path="validation/context/CAP/job-context.json",
            evidence_index=self.index, evidence_index_path="validation/context/CAP/evidence-index.json")
        self.assertEqual((job.prompt, job.prompt_sha256), (original_prompt, original_sha))
        self.assertEqual(value["target_job"]["instruction_sha256"], original_sha)
        self.assertEqual(value["materialization_status"], "READY_BOUNDED")
        authority = next(x for x in value["materialized_items"] if x["authority_class"] != "evidence")
        raw = (self.root / authority["source_ref"]).read_bytes()
        import base64
        self.assertEqual(base64.b64decode(authority["content"]), raw)
        self.assertEqual(authority["content_sha256"], job_context.sha256(raw))
        evidence = next(x for x in value["materialized_items"] if x["authority_class"] == "evidence")
        self.assertEqual(json.loads(evidence["content"])["findings"][0]["finding_id"], "F-1")

    def test_refined_phase_2b_set_is_consumed_without_reselection(self):
        sources = self.declaration["capabilities"]["CAP"]["sources"][:2]
        sources[0].update(always_required=False, target_files=["src/bootstrap.py"])
        sources[1].update(always_required=False, target_files=["src/other.py"])
        self.conditional_sources(sources)
        context, _ = self.build(job=self.job({"target_files": ["src/bootstrap.py"],
                                               "max_text_bytes": 100000}))
        write_json(self.dest / "job-context.json", context)
        value, _ = context_materializer.build(
            self.root, workspace="ws", capability="CAP", job_context=context,
            job_context_path="validation/context/CAP/job-context.json",
            evidence_index=self.index,
            evidence_index_path="validation/context/CAP/evidence-index.json")
        refs = {x["source_ref"] for x in value["materialized_items"]}
        self.assertIn("bootstrap-contract.md", refs)
        self.assertNotIn("bootstrap-adr.md", refs)

    def test_irrelevant_and_optional_are_not_materialized(self):
        index = copy.deepcopy(self.index)
        extra = copy.deepcopy(index["entries"][0]); extra["evidence_id"] = "other"
        extra["capability"] = "OTHER"; extra["evidence_path"] = "evidence/other.json"
        extra["findings"] = []
        index["entries"].append(extra)
        write_json(self.dest / "evidence-index.json", index)
        _, value, _ = self.materialize(index=index)
        refs = {x["evidence_ref"] for x in value["materialized_items"]}
        self.assertNotIn("other", refs)
        self.assertTrue(any(x["reason"] == "IRRELEVANT_PHASE_2B_OMISSION"
                            for x in value["omitted_or_unsupported_items"]))
        _, value2, _ = self.materialize({"max_text_bytes": 100000})
        self.assertFalse(any(x["authority_class"] == "evidence" for x in value2["materialized_items"]))
        self.assertTrue(any(x["reason"] == "OPTIONAL_BOUNDED_CANDIDATE_NOT_PROMOTED"
                            for x in value2["omitted_or_unsupported_items"]))

    def test_hash_mismatch_fails_closed(self):
        context, _, _ = self.materialize()
        (self.root / "bootstrap-contract.md").write_text("tampered", encoding="utf-8")
        value, _ = context_materializer.build(self.root, workspace="ws", capability="CAP",
            job_context=context, job_context_path="validation/context/CAP/job-context.json",
            evidence_index=self.index, evidence_index_path="validation/context/CAP/evidence-index.json")
        self.assertEqual(value["materialization_status"], "UNVERIFIABLE")
        self.assertEqual(value["materialized_items"], [])

    def test_reconciliation_required_job_context_fails_closed(self):
        delta = dict(self.delta)
        delta.update(delta_status="POTENTIAL_AUTHORITY_CHANGE", changed_sources=[{
            "source": "bootstrap-contract.md", "authority": "authoritative"}])
        context, _ = self.build(job=self.job({"max_text_bytes": 100000}), delta=delta)
        write_json(self.dest / "job-context.json", context)
        value, _ = context_materializer.build(
            self.root, workspace="ws", capability="CAP", job_context=context,
            job_context_path="validation/context/CAP/job-context.json",
            evidence_index=self.index,
            evidence_index_path="validation/context/CAP/evidence-index.json")
        self.assertEqual(context["selection_status"], "NEEDS_RECONCILIATION")
        self.assertEqual(value["materialization_status"], "UNVERIFIABLE")
        self.assertEqual(value["materialized_items"], [])

    def test_path_traversal_and_symlink_rejected(self):
        with self.assertRaises(ValueError):
            context_materializer.safe_file(self.root, "../escape")
        if hasattr(os, "symlink"):
            try:
                os.symlink(self.root / "bootstrap-contract.md", self.root / "linked")
            except OSError:
                self.skipTest("symlink unavailable")
            with self.assertRaises(ValueError):
                context_materializer.safe_file(self.root, "linked")

    def test_over_budget_is_nonready_and_nothing_is_truncated(self):
        context, _ = self.build(job=self.job({"max_text_bytes": 100000, "finding_ids": ["F-1"]}))
        context["size"]["configured_max_text_bytes"] = 1
        material = dict(context); material.pop("manifest_sha256"); material.pop("package_sha256")
        identity = job_context.sha256(job_context.canonical(material))
        context["manifest_sha256"] = identity; context["package_sha256"] = identity
        write_json(self.dest / "job-context.json", context)
        value, _ = context_materializer.build(self.root, workspace="ws", capability="CAP",
            job_context=context, job_context_path="validation/context/CAP/job-context.json",
            evidence_index=self.index, evidence_index_path="validation/context/CAP/evidence-index.json")
        self.assertEqual(value["materialization_status"], "NEEDS_EXPANSION")
        self.assertEqual(value["materialized_items"], [])
        self.assertTrue(any(x["reason_code"] == "REQUIRED_ITEMS_EXCEED_BUDGET"
                            for x in value["expansion_requirements"]))

    def test_binary_required_is_explicit_nonready(self):
        (self.root / "bootstrap-contract.md").write_bytes(b"\xff\x00")
        self.manifest = __import__("context_harness").observe(self.root, "CAP")
        self.delta = __import__("context_harness").scan(self.manifest, self.manifest)
        _, value, _ = self.materialize()
        self.assertEqual(value["materialization_status"], "NEEDS_EXPANSION")
        self.assertTrue(any(x["reason"] == "UNSUPPORTED_BINARY_REQUIRED_SOURCE"
                            for x in value["omitted_or_unsupported_items"]))

    def test_partial_required_preserves_expansion(self):
        index = copy.deepcopy(self.index); index["entries"][0]["quality"] = "PARTIAL"
        write_json(self.dest / "evidence-index.json", index)
        context, _ = self.build(job=self.job({"max_text_bytes": 100000, "finding_ids": ["F-1"]}), index=index)
        self.assertEqual(context["selection_status"], "NEEDS_RECONCILIATION")
        write_json(self.dest / "job-context.json", context)
        value, _ = context_materializer.build(self.root, workspace="ws", capability="CAP",
            job_context=context, job_context_path="validation/context/CAP/job-context.json",
            evidence_index=index, evidence_index_path="validation/context/CAP/evidence-index.json")
        self.assertEqual(value["materialization_status"], "UNVERIFIABLE")
        self.assertTrue(any(x["requirement"] == "REQUIRED_BEFORE_REVIEW"
                            for x in value["expansion_requirements"]))

    def test_deterministic_and_changed_source_changes_identity(self):
        _, one, _ = self.materialize(); _, two, _ = self.materialize()
        self.assertEqual(context_materializer.canonical(one), context_materializer.canonical(two))
        old = one["materialized_context_sha256"]
        (self.root / "bootstrap-contract.md").write_text("changed\n", encoding="utf-8")
        self.manifest = __import__("context_harness").observe(self.root, "CAP")
        self.delta = __import__("context_harness").scan(self.manifest, self.manifest)
        _, changed, _ = self.materialize()
        self.assertNotEqual(old, changed["materialized_context_sha256"])

    def test_missing_budget_fails_closed(self):
        _, value, _ = self.materialize({"finding_ids": ["F-1"]})
        self.assertEqual(value["materialization_status"], "NEEDS_EXPANSION")
        self.assertTrue(any(x["code"] == "MISSING_EXPLICIT_BYTE_BUDGET" for x in value["diagnostics"]))

    def test_shadow_exposes_status_without_mutating_job(self):
        import context_harness
        job = self.job({"max_text_bytes": 100000})
        prompt_identity = (job.prompt, job.prompt_sha256)
        session = context_harness.begin_shadow(
            self.root, workspace="ws", actor="codex", mode="execute",
            cache_root=self.root / "cache", job=job)
        result = context_harness.finish_shadow(self.root, session, job_id=job.job_id)
        self.assertEqual(result["materialized_context"]["status"], "READY_BOUNDED")
        self.assertEqual((job.prompt, job.prompt_sha256), prompt_identity)
        self.assertEqual(result["materialized_context"]["mode"], "COMPARISON_ONLY")
