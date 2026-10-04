import copy

import context_harness
import context_materializer
import job_context
from tests.test_job_context import JobContextTests, write_json


class ProjectionFirstContextTests(JobContextTests):
    def configure_projection(self, *, expected=True, provenance=True, sections=False):
        raw_path = self.root / "design-source.md"
        projection_path = self.root / "structured-design.md"
        raw_path.write_text("raw upstream secret design bytes\n", encoding="utf-8")
        projection_path.write_text("# Wanted\nprojected rules\n# Other\nother\n", encoding="utf-8")
        raw_hash = job_context.sha256(raw_path.read_bytes())
        raw = {"path": "design-source.md", "kind": "design_source",
               "authority": "authoritative", "source_role": "PROVENANCE_SOURCE",
               "required": True}
        projection = {"path": "structured-design.md", "kind": "structured_design_data",
            "authority": "authoritative", "source_role": "STRUCTURED_PROJECTION",
            "always_required": False, "context_items": ["rules"],
            "target_files": ["src/rules.py"], "required": True,
            "projection_provenance": ([{"source_ref": "design-source.md",
                **({"expected_sha256": raw_hash} if expected else {"expected_version": "v1"})}]
                if provenance else [])}
        if sections:
            projection.update(section_coverage="COMPLETE_MAPPED", sections=[
                {"section_id": "wanted", "boundary": {"kind": "heading", "heading": "Wanted", "level": 1},
                 "context_items": ["rules"], "target_files": [], "depends_on": []},
                {"section_id": "other", "boundary": {"kind": "heading", "heading": "Other", "level": 1},
                 "context_items": [], "target_files": [], "depends_on": []},
            ])
        existing = self.declaration["capabilities"]["CAP"]["sources"][:2]
        self.declaration["capabilities"]["CAP"]["sources"] = existing + [raw, projection]
        write_json(self.root / ".agent/context.json", self.declaration)
        self.manifest = context_harness.observe(self.root, "CAP")
        self.delta = context_harness.scan(self.manifest, self.manifest)
        return raw_path, projection_path

    def project(self, request):
        context, _ = self.build(self.job(request))
        write_json(self.dest / "job-context.json", context)
        materialized, _ = context_materializer.build(self.root, workspace="ws", capability="CAP",
            job_context=context, job_context_path="validation/context/CAP/job-context.json",
            evidence_index=self.index, evidence_index_path="validation/context/CAP/evidence-index.json")
        return context, materialized

    def test_legacy_declaration_defaults_to_materialized_context(self):
        context, _ = self.build(self.job({"context_items": ["runtime"]}))
        self.assertTrue(all(x["source_role"] == "MATERIALIZED_CONTEXT"
                            for x in context["selector_contract"]["sources"]))

    def test_provenance_excluded_projection_selected_and_raw_payload_zero(self):
        self.configure_projection()
        context, value = self.project({"context_items": ["rules"]})
        self.assertEqual(context["selection_status"], "READY_BOUNDED")
        self.assertEqual([x["source_ref"] for x in context["provenance_source_refs"]], ["design-source.md"])
        self.assertIn("structured-design.md", [x["source_ref"] for x in context["authoritative_source_refs"]])
        self.assertNotIn("design-source.md", [x["source_ref"] for x in context["authoritative_source_refs"]])
        self.assertEqual(value["raw_provenance_payload_bytes"], 0)
        payload = b"".join(__import__("base64").b64decode(x["content"])
                            for x in value["materialized_items"] if x["content_encoding"] == "base64")
        self.assertNotIn(b"raw upstream secret", payload)

    def test_projection_section_slicing_and_provenance_preserved(self):
        self.configure_projection(sections=True)
        context, value = self.project({"context_items": ["rules"]})
        ref = next(x for x in context["authoritative_source_refs"]
                   if x["source_ref"] == "structured-design.md")
        self.assertEqual(ref["selected_section_ids"], ["wanted"])
        item = next(x for x in value["materialized_items"]
                    if x["source_ref"] == "structured-design.md")
        self.assertEqual(item["section_ids"], ["wanted"])
        self.assertEqual(item["parent_source_sha256"], ref["parent_source_sha256"])

    def test_stale_projection_fails_closed_without_raw_fallback(self):
        raw, _ = self.configure_projection()
        raw.write_text("changed upstream\n", encoding="utf-8")
        self.manifest = context_harness.observe(self.root, "CAP")
        self.delta = context_harness.scan(self.manifest, self.manifest)
        context, value = self.project({"context_items": ["rules"]})
        self.assertEqual(context["selection_status"], "PROJECTION_STALE")
        self.assertEqual(value["materialization_status"], "PROJECTION_STALE")
        self.assertEqual(value["materialized_items"], [])
        self.assertEqual(value["raw_provenance_payload_bytes"], 0)

    def test_unverifiable_projection_provenance_fails_closed(self):
        self.configure_projection(provenance=False)
        context, value = self.project({"context_items": ["rules"]})
        self.assertEqual(context["selection_status"], "PROJECTION_PROVENANCE_UNVERIFIABLE")
        self.assertEqual(value["materialization_status"], "PROJECTION_PROVENANCE_UNVERIFIABLE")

    def test_missing_projection_coverage_has_exact_diagnostics_and_no_fallback(self):
        self.configure_projection()
        context, value = self.project({"context_items": ["missing"],
                                       "target_files": ["src/missing.py"]})
        self.assertEqual(context["selection_status"], "PROJECTION_UPDATE_REQUIRED")
        self.assertEqual(context["projection_update_required"], {
            "context_items": ["missing"], "target_files": ["src/missing.py"]})
        self.assertEqual(value["materialized_items"], [])
        self.assertEqual(value["raw_provenance_payload_bytes"], 0)

    def test_upstream_and_projection_changes_affect_identity(self):
        raw, projection = self.configure_projection()
        first, _ = self.build(self.job({"context_items": ["rules"]}))
        first_id = first["package_sha256"]
        raw.write_text("upstream changed\n", encoding="utf-8")
        self.manifest = context_harness.observe(self.root, "CAP")
        self.delta = context_harness.scan(self.manifest, self.manifest)
        upstream, _ = self.build(self.job({"context_items": ["rules"]}))
        self.assertNotEqual(first_id, upstream["package_sha256"])
        projection.write_text("# Wanted\nprojection changed\n", encoding="utf-8")
        self.manifest = context_harness.observe(self.root, "CAP")
        self.delta = context_harness.scan(self.manifest, self.manifest)
        changed, _ = self.build(self.job({"context_items": ["rules"]}))
        self.assertNotEqual(upstream["package_sha256"], changed["package_sha256"])

    def test_projection_budget_exact_fit_and_one_byte_over(self):
        self.configure_projection(sections=True)
        _, baseline = self.project({"context_items": ["rules"]})
        required = baseline["measurement"]["required_payload_bytes"]
        self.declaration["capabilities"]["CAP"]["context"]["max_text_bytes"] = required
        write_json(self.root / ".agent/context.json", self.declaration)
        self.manifest = context_harness.observe(self.root, "CAP")
        self.delta = context_harness.scan(self.manifest, self.manifest)
        _, exact = self.project({"context_items": ["rules"]})
        self.assertEqual(exact["materialization_status"], "READY_BOUNDED")
        _, over = self.project({"context_items": ["rules"], "max_text_bytes": required - 1})
        self.assertEqual(over["materialization_status"], "NEEDS_EXPANSION")
        self.assertEqual(over["materialized_items"], [])
