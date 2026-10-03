import base64
import copy

import context_harness
import context_materializer
import section_slicing
from tests.test_job_context import JobContextTests, write_json


def section(section_id, boundary, **kwargs):
    return {"section_id": section_id, "boundary": boundary,
            "context_items": kwargs.pop("context_items", []), **kwargs}


class SectionResolverTests(__import__("unittest").TestCase):
    def test_exact_preamble_nested_next_boundary_utf8_and_crlf(self):
        raw = "序文\r\n# One\r\nα\r\n## Nested\r\nβ\r\n# Two\r\nend\r\n".encode()
        specs = [
            section("pre", {"kind": "preamble"}),
            section("one", {"kind": "heading", "heading": "One", "level": 1}),
            section("nested", {"kind": "heading", "heading": "Nested", "level": 2}),
            section("two", {"kind": "heading", "heading": "Two", "level": 1}),
        ]
        values = {x["section_id"]: x for x in section_slicing.resolve(raw, specs)}
        self.assertEqual(base64.b64decode(values["pre"]["slice_payload"]), "序文\r\n".encode())
        self.assertEqual(base64.b64decode(values["one"]["slice_payload"]),
                         "# One\r\nα\r\n## Nested\r\nβ\r\n".encode())
        self.assertEqual(values["nested"]["end_byte"], raw.index(b"# Two"))
        self.assertEqual(raw[values["two"]["start_byte"]:values["two"]["end_byte"]], b"# Two\r\nend\r\n")

    def test_duplicate_ambiguous_missing_and_occurrence(self):
        raw = b"# Same\na\n# Same\nb\n"
        ambiguous = [section("x", {"kind": "heading", "heading": "Same", "level": 1})]
        with self.assertRaisesRegex(section_slicing.SectionError, "SECTION_BOUNDARY_AMBIGUOUS"):
            section_slicing.resolve(raw, ambiguous)
        missing = [section("x", {"kind": "heading", "heading": "Nope", "level": 1})]
        with self.assertRaisesRegex(section_slicing.SectionError, "SECTION_BOUNDARY_MISSING"):
            section_slicing.resolve(raw, missing)
        resolved = section_slicing.resolve(raw, [section("x", {"kind": "heading",
            "heading": "Same", "level": 1, "occurrence": 2})])[0]
        self.assertEqual(base64.b64decode(resolved["slice_payload"]), b"# Same\nb\n")

    def test_selectors_dependency_order_and_nested_dedup(self):
        raw = b"# Parent\np\n## Child\nc\n# Other\no\n"
        spec = {"path": "x.md", "kind": "contract", "section_coverage": "COMPLETE_MAPPED",
            "sections": [
                section("parent", {"kind": "heading", "heading": "Parent", "level": 1},
                        context_items=["parent"], depends_on=["child"]),
                section("child", {"kind": "heading", "heading": "Child", "level": 2},
                        target_files=["src/x.py"]),
                section("other", {"kind": "heading", "heading": "Other", "level": 1},
                        always_required=True),
            ]}
        selected = section_slicing.select(spec, raw, context_items=["parent"], target_files=[])
        self.assertEqual([x["section_id"] for x in selected["sections"]], ["parent", "child", "other"])
        merged = section_slicing.merge_selected(selected["sections"], raw)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["section_ids"], ["parent", "child"])

    def test_incomplete_empty_and_unsupported_fail_to_whole(self):
        base = {"path": "x.md", "kind": "contract", "section_coverage": "COMPLETE_MAPPED",
            "sections": [section("x", {"kind": "heading", "heading": "X", "level": 1},
                                  context_items=["known"])]}
        result = section_slicing.select(base, b"# X\nx\n", context_items=["unknown"], target_files=[])
        self.assertEqual(result["coverage_status"], "SECTION_COVERAGE_INCOMPLETE")
        empty = section_slicing.select(base, b"# X\nx\n", context_items=[], target_files=[])
        self.assertEqual(empty["sections"], [])
        unsupported = copy.deepcopy(base); unsupported.update(path="x.pdf", kind="pdf")
        self.assertEqual(section_slicing.select(unsupported, b"# X\n", context_items=["known"],
            target_files=[])["coverage_status"], "SECTION_SLICING_UNSUPPORTED_SOURCE")


class SectionIntegrationTests(JobContextTests):
    def enable_sections(self):
        source = self.declaration["capabilities"]["CAP"]["sources"][0]
        source.update(section_coverage="COMPLETE_MAPPED", sections=[
            section("intro", {"kind": "preamble"}, always_required=True),
            section("wanted", {"kind": "heading", "heading": "Wanted", "level": 1},
                    context_items=["rules"]),
            section("other", {"kind": "heading", "heading": "Other", "level": 1,
                              "occurrence": 1}, target_files=["src/other.py"]),
        ])
        (self.root / "bootstrap-contract.md").write_bytes(b"preamble\r\n# Wanted\r\nkeep\r\n# Other\r\nskip\r\n")
        write_json(self.root / ".agent/context.json", self.declaration)
        self.manifest = context_harness.observe(self.root, "CAP")
        self.delta = context_harness.scan(self.manifest, self.manifest)

    def materialized(self, request, budget=100000):
        self.enable_sections()
        request = dict(request); request["max_text_bytes"] = budget
        context, _ = self.build(self.job(request))
        write_json(self.dest / "job-context.json", context)
        value, report = context_materializer.build(self.root, workspace="ws", capability="CAP",
            job_context=context, job_context_path="validation/context/CAP/job-context.json",
            evidence_index=self.index, evidence_index_path="validation/context/CAP/evidence-index.json")
        return context, value, report

    def test_context_selection_provenance_identity_and_budget(self):
        context, value, _ = self.materialized({"context_items": ["rules"]})
        ref = next(x for x in context["authoritative_source_refs"] if x["source_ref"] == "bootstrap-contract.md")
        self.assertEqual(ref["selected_section_ids"], ["intro", "wanted"])
        self.assertEqual(ref["parent_source_sha256"], ref["raw_sha256"])
        sliced = [x for x in value["materialized_items"] if x.get("section_ids")]
        self.assertEqual([x["section_ids"] for x in sliced], [["intro"], ["wanted"]])
        self.assertEqual(value["measurement"]["selected_slice_bytes"],
                         26 + len((self.root / "bootstrap-adr.md").read_bytes()))
        self.assertGreater(value["measurement"]["avoided_bytes"], 0)

    def test_exact_fit_one_over_and_no_truncation(self):
        _, baseline, _ = self.materialized({"context_items": ["rules"]})
        required = baseline["measurement"]["required_payload_bytes"]
        self.declaration["capabilities"]["CAP"]["context"]["max_text_bytes"] = required
        # materialized() rewrites the section contract while preserving this budget.
        _, exact, _ = self.materialized({"context_items": ["rules"]}, required)
        self.assertEqual(exact["materialization_status"], "READY_BOUNDED")
        _, over, _ = self.materialized({"context_items": ["rules"]}, required - 1)
        self.assertEqual(over["materialization_status"], "NEEDS_EXPANSION")
        self.assertEqual(over["materialized_items"], [])

    def test_boundary_failure_falls_back_whole_and_provenance_tamper_fails(self):
        self.enable_sections()
        self.declaration["capabilities"]["CAP"]["sources"][0]["sections"][1]["boundary"]["heading"] = "Missing"
        write_json(self.root / ".agent/context.json", self.declaration)
        self.manifest = context_harness.observe(self.root, "CAP"); self.delta = context_harness.scan(self.manifest, self.manifest)
        context, _ = self.build(self.job({"max_text_bytes": 100000, "context_items": ["rules"]}))
        ref = next(x for x in context["authoritative_source_refs"] if x["source_ref"] == "bootstrap-contract.md")
        self.assertEqual(ref["section_coverage_status"], "SECTION_BOUNDARY_MISSING")
        self.assertEqual(ref["selected_sections"], [])
        write_json(self.dest / "job-context.json", context)
        value, _ = context_materializer.build(self.root, workspace="ws", capability="CAP", job_context=context,
            job_context_path="validation/context/CAP/job-context.json", evidence_index=self.index,
            evidence_index_path="validation/context/CAP/evidence-index.json")
        item = next(x for x in value["materialized_items"] if x["source_ref"] == "bootstrap-contract.md")
        self.assertEqual(item["byte_count"], ref["whole_file_bytes"])

    def test_parent_change_remains_reconciliation(self):
        self.enable_sections()
        previous = self.manifest
        (self.root / "bootstrap-contract.md").write_bytes(b"preamble\r\n# Wanted\r\nchanged\r\n# Other\r\nskip\r\n")
        current = context_harness.observe(self.root, "CAP")
        delta = context_harness.scan(previous, current)
        self.assertEqual(delta["delta_status"], "POTENTIAL_AUTHORITY_CHANGE")

    def test_slice_provenance_mismatch_fails_closed(self):
        context, _, _ = self.materialized({"context_items": ["rules"]})
        ref = next(x for x in context["authoritative_source_refs"]
                   if x["source_ref"] == "bootstrap-contract.md")
        ref["selected_sections"][0]["slice_payload"] = base64.b64encode(b"wrong").decode()
        context.pop("package_sha256"); context.pop("manifest_sha256")
        identity = __import__("job_context").sha256(__import__("job_context").canonical(context))
        context["package_sha256"] = identity; context["manifest_sha256"] = identity
        write_json(self.dest / "job-context.json", context)
        value, _ = context_materializer.build(self.root, workspace="ws", capability="CAP",
            job_context=context, job_context_path="validation/context/CAP/job-context.json",
            evidence_index=self.index, evidence_index_path="validation/context/CAP/evidence-index.json")
        self.assertEqual(value["materialization_status"], "UNVERIFIABLE")
        self.assertEqual(value["materialized_items"], [])
        self.assertTrue(any(x["code"] == "SECTION_PROVENANCE_MISMATCH"
                            for x in value["diagnostics"]))
