import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import evidence_index as indexer


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


class EvidenceIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=".tmp-tests")
        self.root = Path(self.temp.name).resolve()
        self.review_root = self.root / "evidence/reviews"
        self.job_root = self.root / "evidence/jobs"
        self.review_root.mkdir(parents=True)
        self.job_root.mkdir(parents=True)
        self.cache = self.root / "cache/index.json"

    def tearDown(self):
        self.temp.cleanup()

    def review(self, job="REVIEW-1", *, execution=None, actor_status="DONE",
               boundary="CLEAN", decision=None):
        package = self.review_root / job
        package.mkdir()
        execution = execution or {"events": [], "final_result_text": "six prose findings",
                                  "exit_code": 0}
        files = {
            "review-input.json": canonical({"input_manifest_sha256": "a" * 64,
                                             "file_count": 1, "files": []}),
            "review-execution.json": canonical(execution),
        }
        if decision is None and "findings" in execution:
            decision = {"schema":"normalized-review-decision","schema_version":1,
                "review_job_id":job,"actor":"claude","workspace":"fixture","capability":"CAP",
                "review_mode":"FULL_REVIEW","verdict":execution.get("review_verdict"),
                "findings":[{**f,"trust_class":"ACTOR_REPORTED"} for f in execution["findings"]],
                "provenance":{},"trust_class":"ACTOR_REPORTED"}
        if decision is not None:
            files["review-decision.json"] = canonical(decision)
        for name in ("canonical-diff-head-before.stat", "canonical-diff-head-after.stat",
                     "review-diff-head-before.stat", "review-diff-head-after.stat"):
            files[name] = b""
        for name, data in files.items():
            (package / name).write_bytes(data)
        manifest = {
            "schema": "worker-review-evidence", "version": 1, "job_id": job,
            "actor": "claude", "mode": "review", "canonical_head": "c" * 40,
            "review": {"head_before": "c" * 40, "head_after": "c" * 40},
            "review_boundary": boundary, "actor_status": actor_status,
            "normalized_files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest()}
                                 for name, data in files.items()],
        }
        if decision is not None:
            manifest["review_decision"] = {
                "path": "review-decision.json",
                "sha256": hashlib.sha256(files["review-decision.json"]).hexdigest(),
                "finding_count": len(decision["findings"]), "trust": "ACTOR_REPORTED"}
        (package / "review-manifest.json").write_bytes(canonical(manifest))
        return package

    def historical(self, job="CODEX-1"):
        package = self.job_root / job
        package.mkdir()
        normalized = canonical({"schema": "normalized-historical-job-result", "version": 1,
            "worker_observed": {"status": "DONE", "artifact_status": "DONE",
                                "git": {"baseline_commit": "a", "head_after": "b"}}})
        actor = canonical({"authority": "ACTOR_REPORTED", "summary": "correction"})
        refs = [{"path": "actor-reported.json", "sha256": hashlib.sha256(actor).hexdigest()},
                {"path": "normalized-result.json", "sha256": hashlib.sha256(normalized).hexdigest()}]
        manifest = {"schema": "worker-job-evidence", "version": 1, "job_id": job,
                    "actor": "codex", "mode": "implementation", "workspace": "fixture",
                    "instruction_sha256": "d" * 64,
                    "adoption": {"mode": "HISTORICAL_MANUAL"},
                    "worker_observed": {"file": "normalized-result.json",
                                        "sha256": hashlib.sha256(normalized).hexdigest()},
                    "adopted_files": refs,
                    "trust_limitation": "not a standalone safety gate"}
        (package / "normalized-result.json").write_bytes(normalized)
        (package / "actor-reported.json").write_bytes(actor)
        (package / "job-evidence-manifest.json").write_bytes(canonical(manifest))
        return package

    def historical_review(self, job="HISTORICAL-REVIEW", finding_ids=("F-1",)):
        package = self.review_root / job
        package.mkdir()
        source_sha = "f" * 64
        decision = {"schema":"normalized-review-decision","schema_version":1,
            "review_job_id":job,"actor":"claude","workspace":"fixture","capability":"CAP",
            "review_mode":"FULL_REVIEW","verdict":"CHANGES_REQUESTED",
            "findings":[{"finding_id":fid,"status":"OPEN","trust_class":"ACTOR_REPORTED"}
                        for fid in finding_ids],
            "provenance":{"normalization_mode":"HISTORICAL_MANUAL","source_job_id":job,
                "source_file":"review-execution.json","source_sha256":source_sha,
                "human_approved":True},"trust_class":"ACTOR_REPORTED"}
        data = canonical(decision)
        (package / "review-decision.json").write_bytes(data)
        manifest = {"schema":"worker-review-evidence","version":1,"job_id":job,
            "actor":"claude","mode":"review","workspace":"fixture","actor_status":"DONE",
            "review_boundary":"HISTORICAL_UNKNOWN",
            "adoption":{"mode":"HISTORICAL_MANUAL","human_approved":True},
            "normalized_files":[{"path":"review-decision.json",
                                  "sha256":hashlib.sha256(data).hexdigest()}],
            "review_decision":{"path":"review-decision.json",
                "sha256":hashlib.sha256(data).hexdigest(),
                "finding_count":len(finding_ids),"trust":"ACTOR_REPORTED"},
            "source_provenance":{"source_job_id":job,"source_sha256":source_sha,
                                  "source_storage":"LOCAL_ONLY_NOT_COPIED"},
            "raw_local_only":[{"path":"review-execution.json","sha256":source_sha,
                                "storage":"LOCAL_ONLY"}],
            "trust_limitation":"actor judgment only"}
        (package / "review-manifest.json").write_bytes(canonical(manifest))
        return package

    def sources(self):
        return indexer.discover_sources(self.root, [
            {"path": "evidence/reviews", "kind": "review",
             "manifest_name": "review-manifest.json"},
            {"path": "evidence/jobs", "kind": "historical_job",
             "manifest_name": "job-evidence-manifest.json"},
        ])

    def build(self):
        return indexer.build_index(self.root, workspace="fixture", capability="CAP",
                                   sources=self.sources(), cache_path=self.cache)

    def test_deterministic_hash_order_and_incremental_reuse(self):
        self.historical("Z-JOB"); self.review("A-REVIEW")
        first, report1 = self.build(); second, report2 = self.build()
        self.assertEqual(hashlib.sha256(canonical(first)).hexdigest(), report1["index_sha256"])
        self.assertEqual(first, second)
        self.assertEqual(report2["reused_source_count"], 2)
        self.assertEqual([x["path"] for x in first["source_manifest"]],
                         sorted(x["path"] for x in first["source_manifest"]))

    def test_add_remove_replace_detection(self):
        package = self.review("ONE")
        _, first = self.build()
        self.historical("TWO")
        _, added = self.build()
        self.assertEqual((first["source_count"], added["changed_source_count"]), (1, 1))
        (package / "review-manifest.json").unlink()
        package.rmdir() if not any(package.iterdir()) else None
        # Remove the complete package, as canonical adoption would.
        for child in list(package.iterdir()): child.unlink()
        package.rmdir()
        _, removed = self.build()
        self.assertTrue(any("ONE" in path for path in removed["removed_sources"]))
        manifest = self.job_root / "TWO/job-evidence-manifest.json"
        value = json.loads(manifest.read_text())
        value["instruction_sha256"] = "e" * 64
        manifest.write_bytes(canonical(value))
        _, replaced = self.build()
        self.assertEqual(replaced["changed_source_count"], 1)

    def test_quality_classes_and_no_prose_finding_invention(self):
        self.review("PROSE")
        self.review("PARTIAL", execution={"events": [], "final_result_text": None, "exit_code": 0})
        self.review("STRUCTURED", execution={"events": [], "final_result_text": "ok", "exit_code": 0,
            "review_verdict": "CHANGES_REQUESTED",
            "findings": [{"finding_id": "F-2", "status": "OPEN"},
                         {"finding_id": "F-1", "status": "CLOSED"}]})
        index, report = self.build()
        entries = {e["job_id"]: e for e in index["entries"]}
        self.assertEqual(entries["PROSE"]["quality"], "UNSTRUCTURED")
        self.assertIsNone(entries["PROSE"]["findings"])
        self.assertEqual(entries["PARTIAL"]["quality"], "PARTIAL")
        self.assertEqual(entries["STRUCTURED"]["quality"], "STRUCTURED")
        self.assertEqual([f["finding_id"] for f in entries["STRUCTURED"]["findings"]],
                         ["F-1", "F-2"])
        self.assertEqual(report["quality_counts"],
                         {"STRUCTURED": 1, "PARTIAL": 1, "UNSTRUCTURED": 1})

    def test_argus_bootstrap_429_is_execution_failure_not_verdict(self):
        self.review("ARGUS-BOOTSTRAP-CONTRACT-V01-REVIEW-RETRY-20261003-001")
        self.historical("ARGUS-BOOTSTRAP-CONTRACT-V01-CORRECTION-20261003-001")
        result = {"job_id": "ARGUS-BOOTSTRAP-CONTRACT-V01-REREVIEW-20261003-001",
                  "actor": "claude", "mode": "review", "workspace": "argus",
                  "status": "FAILED", "failure_class": "SESSION_LIMIT",
                  "review_verdict": "APPROVED",  # must be suppressed on failure
                  "review_execution": {"actor_status": "AGENT_ERROR"},
                  "review_boundary": {"status": "CLEAN"},
                  "summary": "429 session limit"}
        path = self.root / "canonical-result.json"; path.write_bytes(canonical(result))
        index, _ = indexer.build_index(self.root, workspace="argus",
            capability="RUNTIME-BOOTSTRAP-ORCHESTRATOR",
            sources=self.sources() +
                    [{"path": "canonical-result.json", "kind": "review", "required": True}])
        entry = next(e for e in index["entries"] if e["job_id"].endswith("REREVIEW-20261003-001"))
        self.assertEqual(entry["failure_class"], "SESSION_LIMIT")
        self.assertEqual(entry["actor_execution_status"], "AGENT_ERROR")
        self.assertIsNone(entry["review_verdict"])
        prior = next(e for e in index["entries"] if e["job_id"].endswith("REVIEW-RETRY-20261003-001"))
        correction = next(e for e in index["entries"] if "CORRECTION" in e["job_id"])
        self.assertEqual(prior["quality"], "UNSTRUCTURED")
        self.assertIsNone(prior["findings"])
        self.assertEqual(correction["evidence_type"], "historical_job")
        self.assertEqual(len(index["source_manifest"]), 3)

    def test_historical_trust_and_structured_review_boundary_preserved(self):
        self.historical(); self.review()
        index, _ = self.build()
        historical = next(e for e in index["entries"] if e["evidence_type"] == "historical_job")
        review = next(e for e in index["entries"] if e["evidence_type"] == "review")
        self.assertIn("HISTORICAL_MANUAL", historical["trust"])
        self.assertIn("CORROBORATIVE_ONLY", historical["trust"])
        self.assertIn("not a standalone safety gate", historical["trust_limitation"])
        self.assertEqual((review["review_boundary"], review["actor_execution_status"]),
                         ("CLEAN", "DONE"))

    def test_historical_structured_review_has_no_fabricated_execution_facts(self):
        self.historical_review(finding_ids=tuple(f"F-{n}" for n in range(1, 7)))
        index, report = self.build()
        self.assertEqual(report["status"], "READY")
        entry = next(e for e in index["entries"] if e["job_id"] == "HISTORICAL-REVIEW")
        self.assertEqual(entry["quality"], "STRUCTURED")
        self.assertEqual([f["finding_id"] for f in entry["findings"]],
                         [f"F-{n}" for n in range(1, 7)])
        self.assertEqual(entry["trust"], ["ACTOR_REPORTED", "HISTORICAL_MANUAL"])
        self.assertIsNone(entry["actor_execution_status"])
        self.assertIsNone(entry["review_boundary"])

    def test_live_review_missing_execution_fails_closed(self):
        package = self.review()
        manifest_path = package / "review-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["normalized_files"] = [x for x in manifest["normalized_files"]
                                        if x["path"] != "review-execution.json"]
        (package / "review-execution.json").unlink()
        manifest_path.write_bytes(canonical(manifest))
        _, report = self.build()
        self.assertEqual(report["status"], "FAILED")
        self.assertIn("required normalized evidence missing", report["diagnostics"][0]["detail"])

    def test_historical_decision_source_provenance_mismatch_fails(self):
        package = self.historical_review()
        decision_path = package / "review-decision.json"
        decision = json.loads(decision_path.read_text())
        decision["provenance"]["source_sha256"] = "0" * 64
        data = canonical(decision)
        decision_path.write_bytes(data)
        manifest_path = package / "review-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["normalized_files"][0]["sha256"] = hashlib.sha256(data).hexdigest()
        manifest["review_decision"]["sha256"] = hashlib.sha256(data).hexdigest()
        manifest_path.write_bytes(canonical(manifest))
        _, report = self.build()
        self.assertEqual(report["status"], "FAILED")
        self.assertIn("provenance mismatch", report["diagnostics"][0]["detail"])

    def test_malformed_and_hash_mismatch_are_explicit_failures(self):
        package = self.review()
        (package / "review-execution.json").write_text("not json")
        index, report = self.build()
        self.assertEqual((index["status"], report["status"]), ("FAILED", "FAILED"))
        self.assertIn("hash mismatch", report["diagnostics"][0]["detail"])
        manifest = package / "review-manifest.json"
        manifest.write_text("not json")
        index, report = self.build()
        self.assertEqual(index["status"], "FAILED")
        self.assertTrue(report["diagnostics"])

    def test_traversal_and_symlink_escape_rejected(self):
        with self.assertRaises(ValueError):
            indexer.discover_sources(self.root, [{"path": "../outside", "kind": "review",
                                                   "manifest_name": "review-manifest.json"}])
        outside = Path(self.temp.name).parent / (Path(self.temp.name).name + "-outside")
        outside.mkdir(exist_ok=True)
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(ValueError):
            indexer.discover_sources(self.root, [{"path": "linked", "kind": "review",
                                                   "manifest_name": "review-manifest.json"}])

    def test_raw_transcript_is_not_discovered(self):
        (self.review_root / "claude-stream.jsonl").write_text("raw")
        self.review()
        sources = self.sources()
        self.assertEqual(len(sources), 1)
        self.assertNotIn("claude-stream", sources[0]["path"])

    def test_incomplete_canonical_package_is_not_silently_skipped(self):
        (self.review_root / "BROKEN").mkdir()
        with self.assertRaisesRegex(ValueError, "lacks review-manifest.json"):
            self.sources()

    def test_project_generic_generate_and_report_location(self):
        self.historical()
        report = indexer.generate(self.root, workspace="generic", capability="ANY-CAP",
            source_roots=[{"path": "evidence/jobs", "kind": "historical_job",
                           "manifest_name": "job-evidence-manifest.json"}],
            declared_sources=[], generated_root="validation/context", cache_path=self.cache)
        self.assertEqual(report["status"], "READY")
        self.assertTrue((self.root / "validation/context/ANY-CAP/evidence-index.json").is_file())
        self.assertTrue((self.root / report["report_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
