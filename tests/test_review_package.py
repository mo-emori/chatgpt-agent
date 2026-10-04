import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import context_harness
import evidence_index
import job_context
import review_package
import review_invocation
from attribution_policy import attribution_paths
from job_log import get_attributable_changed_paths, get_git_snapshot


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(job_context.canonical(value))


class ReviewPackageTests(unittest.TestCase):
    def setUp(self):
        Path(".tmp-tests").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=".tmp-tests")
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / ".agent").mkdir()
        (self.root / "authority.md").write_text("authority\n", encoding="utf-8")
        (self.root / "implementation.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "unrelated.txt").write_text("dirty baseline\n", encoding="utf-8")
        self.declaration = {"schema_version": 1, "mode": "SHADOW",
            "generated_root": "validation/context", "capabilities": {"CAP": {
                "selectors": {"actors": ["codex"], "modes": ["execute"]},
                "sources": [{"path": "authority.md", "kind": "contract",
                             "authority": "authoritative", "context_items": ["api"],
                             "required": True}]}}}
        write_json(self.root / ".agent/context.json", self.declaration)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)
        # Pre-existing unrelated dirt must not enter the target delta.
        (self.root / "unrelated.txt").write_text("unrelated pre-existing dirt\n", encoding="utf-8")
        self.before = get_git_snapshot(self.root)
        (self.root / "implementation.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.after = get_git_snapshot(self.root)
        self.paths = get_attributable_changed_paths(self.before, self.after)
        self.manifest = context_harness.observe(self.root, "CAP")
        self.delta = context_harness.scan(self.manifest, self.manifest)
        self.dest = self.root / "validation/context/CAP"
        write_json(self.dest / "context-manifest.json", self.manifest)
        write_json(self.dest / "delta-report.json", self.delta)
        findings = [{"finding_id": x, "status": "OPEN"} for x in
                    ("F-1", "F-2", "F-3", "MINOR-1", "MINOR-2", "MINOR-3")]
        self.evidence = {"job_id": "REVIEW-1", "actor": "claude", "mode": "review",
                         "workspace": "ws", "capability": "CAP", "status": "DONE",
                         "review_execution": {"actor_status": "DONE"},
                         "review_verdict": "CHANGES_REQUESTED", "findings": findings}
        write_json(self.root / "evidence/review.json", self.evidence)
        self.index, _ = evidence_index.build_index(self.root, workspace="ws", capability="CAP",
            sources=[{"path": "evidence/review.json", "kind": "review", "required": True}])
        write_json(self.dest / "evidence-index.json", self.index)

    def tearDown(self):
        self.temp.cleanup()

    def job(self, mode="DELTA_REVIEW", findings=None):
        request = {"review_mode": mode, "target_files": ["implementation.py"],
                   "finding_ids": findings or ["F-1", "F-2", "F-3", "MINOR-1", "MINOR-2", "MINOR-3"]}
        return SimpleNamespace(protocol_version="3", job_id="JOB", actor="codex", mode="execute", workspace="ws",
            prompt_sha256="a" * 64, prompt="unchanged",
            instruction_ref={"type": "notion_page", "page_id": "p", "context_request": request})

    def inputs(self, job=None, index=None):
        job, index = job or self.job(), index or self.index
        write_json(self.dest / "evidence-index.json", index)
        jc, _ = job_context.build(self.root, workspace="ws", capability="CAP", job=job,
            manifest=self.manifest, manifest_path="validation/context/CAP/context-manifest.json",
            delta=self.delta, delta_path="validation/context/CAP/delta-report.json",
            evidence_index=index, evidence_index_path="validation/context/CAP/evidence-index.json",
            declaration=self.declaration)
        write_json(self.dest / "job-context.json", jc)
        return dict(workspace="ws", capability="CAP", job=job, manifest=self.manifest,
            manifest_path="validation/context/CAP/context-manifest.json", delta=self.delta,
            delta_path="validation/context/CAP/delta-report.json", evidence_index=index,
            evidence_index_path="validation/context/CAP/evidence-index.json", job_context=jc,
            job_context_path="validation/context/CAP/job-context.json")

    def build(self, **overrides):
        args = self.inputs(overrides.pop("job", None), overrides.pop("index", None))
        args.update(before=self.before, after=self.after, changed_paths=self.paths)
        args.update(overrides)
        return review_package.build(self.root, **args)

    def test_deterministic_layout_hash_attribution_and_metrics(self):
        one, report1 = self.build(); two, report2 = self.build()
        self.assertEqual(one, two)
        self.assertEqual(report1["sha256"], report2["sha256"])
        manifest = json.loads(one["package-manifest.json"])
        self.assertEqual([x["path"] for x in manifest["files"]], list(review_package.PACKAGE_FILES))
        self.assertEqual(manifest["git"]["worker_observed_changed_paths"], ["implementation.py"])
        self.assertNotIn(b"unrelated.txt", one["diff.patch"])
        self.assertIn(b"implementation.py", one["diff.patch"])
        self.assertEqual(report1["file_count"], 9)
        self.assertEqual(report1["ref_count"], 2)
        self.assertEqual(report1["bytes"], sum(len(one[x]) for x in review_package.PACKAGE_FILES))

    def test_dirty_target_is_uncertain_and_not_silently_patched(self):
        before = get_git_snapshot(self.root)
        (self.root / "unrelated.txt").write_text("changed again\n", encoding="utf-8")
        after = get_git_snapshot(self.root)
        payload, report = self.build(before=before, after=after, changed_paths=["unrelated.txt"])
        self.assertEqual(report["attribution_status"], "ATTRIBUTION_UNCERTAIN")
        self.assertEqual(report["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(payload["diff.patch"], b"")

    def test_generated_infrastructure_alone_is_exact_and_ready(self):
        generated = "validation/context/CAP/JOB/post-actor/diagnostic.json"
        paths = attribution_paths(
            [generated], generated_roots=["validation/context/CAP"],
            protected_paths=["implementation.py", "authority.md"])
        payload, report = self.build(changed_paths=paths)
        self.assertEqual(paths, [])
        self.assertEqual(report["attribution_status"], "EXACT")
        self.assertEqual(report["status"], "READY_PACKAGE")
        reasons = [item["reason"] for item in
                   json.loads(payload["expansion-plan.json"])["requirements"]]
        self.assertNotIn("TARGET_PATH_DIRTY_BEFORE_JOB", reasons)

    def test_generated_plus_preexisting_dirty_target_still_reconciles(self):
        (self.root / "implementation.py").write_text("VALUE = dirty\n", encoding="utf-8")
        before = get_git_snapshot(self.root)
        (self.root / "implementation.py").write_text("VALUE = changed again\n", encoding="utf-8")
        after = get_git_snapshot(self.root)
        paths = attribution_paths([
            "validation/context/CAP/JOB/post-actor/diagnostic.json",
            "implementation.py",
        ], generated_roots=["validation/context/CAP"],
            protected_paths=["implementation.py", "authority.md"])
        payload, report = self.build(before=before, after=after, changed_paths=paths)
        self.assertEqual(report["attribution_status"], "ATTRIBUTION_UNCERTAIN")
        self.assertEqual(report["status"], "NEEDS_RECONCILIATION")
        reasons = [item["reason"] for item in
                   json.loads(payload["expansion-plan.json"])["requirements"]]
        self.assertIn("TARGET_PATH_DIRTY_BEFORE_JOB", reasons)

    def test_preexisting_dirty_authority_remains_detected(self):
        (self.root / "authority.md").write_text("dirty authority\n", encoding="utf-8")
        before = get_git_snapshot(self.root)
        (self.root / "authority.md").write_text("changed authority again\n", encoding="utf-8")
        after = get_git_snapshot(self.root)
        paths = attribution_paths(
            ["authority.md"], generated_roots=["validation/context/CAP"],
            protected_paths=["implementation.py", "authority.md"])
        with self.assertRaisesRegex(ValueError, "selected authority ref changed"):
            self.build(before=before, after=after, changed_paths=paths)

    def test_structured_six_findings_and_429_is_not_a_verdict(self):
        payload, report = self.build()
        findings = json.loads(payload["findings.json"])["findings"]
        self.assertEqual([x["finding_id"] for x in findings],
                         ["F-1", "F-2", "F-3", "MINOR-1", "MINOR-2", "MINOR-3"])
        failed = dict(self.evidence, status="FAILED", failure_class="RATE_LIMITED",
                      review_execution={"actor_status": "AGENT_ERROR"}, review_verdict="APPROVED",
                      findings=None, summary="429")
        write_json(self.root / "evidence/review.json", failed)
        index, _ = evidence_index.build_index(self.root, workspace="ws", capability="CAP",
            sources=[{"path": "evidence/review.json", "kind": "review", "required": True}])
        payload, report = self.build(index=index)
        self.assertEqual(json.loads(payload["findings.json"])["findings"], [])
        candidates = json.loads(payload["job-context.json"])["optional_evidence_candidates"]
        self.assertIsNone(candidates[0]["review_verdict"])
        self.assertEqual(report["status"], "NEEDS_RECONCILIATION")

    def test_optional_candidate_is_ready_and_not_duplicated(self):
        entry = dict(self.index["entries"][0])
        entry.update({"quality": "UNSTRUCTURED", "findings": None})
        index = dict(self.index); index["entries"] = [entry]
        job = self.job()
        job.instruction_ref["context_request"]["finding_ids"] = []
        payload, report = self.build(job=job, index=index)
        plan = json.loads(payload["expansion-plan.json"])["requirements"]
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["requirement"], "OPTIONAL_BOUNDED")
        self.assertEqual(report["status"], "READY_PACKAGE")
        self.assertEqual(report["expansion_required_count"], 0)
        self.assertEqual(report["optional_candidate_count"], 1)

    def test_review_modes_boundary_and_full_marker(self):
        delta, _ = self.build(job=self.job("DELTA_REVIEW"))
        boundary, _ = self.build(job=self.job("BOUNDARY_REVIEW"))
        full, report = self.build(job=self.job("FULL_REVIEW"))
        self.assertEqual(json.loads(delta["authority-refs.json"])["declared_boundary_refs"], [])
        self.assertTrue(json.loads(boundary["authority-refs.json"])["declared_boundary_refs"])
        reasons = [x["reason"] for x in json.loads(full["expansion-plan.json"])["requirements"]]
        self.assertIn("FULL_REVIEW_REQUIRES_BROAD_REPO_VISIBILITY", reasons)
        self.assertEqual(report["candidate_review_mode"], "FULL_REVIEW")

    def test_generate_validate_tamper_stale_wrong_identity_and_prompt_unchanged(self):
        job = self.job(); prompt = job.prompt; args = self.inputs(job)
        report = review_package.generate(self.root, generated_root="validation/context", **args,
            before=self.before, after=self.after, changed_paths=self.paths)
        self.assertEqual(job.prompt, prompt)
        ref = {"path": report["package_path"], "sha256": report["sha256"]}
        manifest = review_package.validate_ref(self.root, ref, workspace="ws", capability="CAP",
            current_context_sha256=self.manifest["lifecycle"]["manifest_sha256"],
            current_job_context_sha256=args["job_context"] and
                review_package.sha256(job_context.canonical(args["job_context"])))
        self.assertEqual(manifest["package_sha256"], report["sha256"])
        with self.assertRaises(ValueError):
            review_package.validate_ref(self.root, ref, workspace="wrong", capability="CAP")
        with self.assertRaises(ValueError):
            review_package.validate_ref(self.root, ref, workspace="ws", capability="CAP",
                                        current_context_sha256="0" * 64)
        with self.assertRaises(ValueError):
            review_package.validate_ref(self.root, ref, workspace="ws", capability="CAP",
                                        current_job_context_sha256="0" * 64)
        (self.root / report["package_path"] / "delta.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            review_package.validate_ref(self.root, ref, workspace="ws", capability="CAP")

    def test_included_ref_tamper_and_result_manifest_addition(self):
        job = self.job(); args = self.inputs(job)
        report = review_package.generate(self.root, generated_root="validation/context", **args,
            before=self.before, after=self.after, changed_paths=self.paths)
        ref = {"path": report["package_path"], "sha256": report["sha256"]}
        (self.root / "authority.md").write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            review_package.validate_ref(self.root, ref, workspace="ws", capability="CAP")

        import agent_worker
        response = agent_worker.build_result(job, status="DONE", runtime={},
            context={"review_package": report})
        self.assertEqual(response["review_package"], report)
        self.assertEqual(response["status"], "DONE")

    def test_traversal_and_symlink_escape_rejected(self):
        with self.assertRaises(ValueError):
            review_package.validate_ref(self.root, {"path": "../outside", "sha256": "x"},
                                        workspace="ws", capability="CAP")
        outside = self.root.parent / (self.root.name + "-outside")
        outside.mkdir(exist_ok=True)
        try:
            (self.root / "link").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(ValueError):
            review_package.validate_ref(self.root, {"path": "link/pkg", "sha256": "x"},
                                        workspace="ws", capability="CAP")

    def test_package_first_prompt_expansion_and_usage_telemetry(self):
        job = self.job(); args = self.inputs(job)
        report = review_package.generate(self.root, generated_root="validation/context", **args,
            before=self.before, after=self.after, changed_paths=self.paths)
        ref = {"path": report["package_path"], "sha256": report["sha256"],
               "target_job_id": "JOB"}
        review_job = SimpleNamespace(review_mode="DELTA_REVIEW", measurement_mode=True,
            review_package_ref=ref, workspace="ws", prompt="independent review")
        with patch.object(review_invocation, "observe", return_value=self.manifest):
            launch = review_invocation.prepare(review_job, self.root,
                {"review_measurement": {"enabled": True, "capabilities": ["CAP"]}},
                {"capability": "CAP"})
        prompt = review_invocation.build_prompt(review_job.prompt, launch)
        self.assertIn(report["sha256"], prompt)
        self.assertIn("Do not rediscover or scan the whole repository", prompt)
        self.assertIn("OUT_OF_PACKAGE_SCOPE", prompt)
        raw = json.dumps({"type":"assistant", "message":{"usage":{
            "input_tokens":10,"cache_read_input_tokens":20,"output_tokens":3}, "content":[]}})
        events = [{"kind":"tool_use", "tool":"Read", "path":"outside.txt"},
                  {"kind":"tool_use", "tool":"Bash", "command":"type secret.txt"}]
        telemetry = review_invocation.telemetry(launch, events, raw, "PACKAGE_INSUFFICIENT")
        self.assertEqual(telemetry["package_outcome"], "PACKAGE_INSUFFICIENT")
        self.assertFalse(telemetry["full_review_escalated"])
        self.assertFalse(telemetry["measurement_complete"])
        self.assertIn("OUT_OF_PACKAGE_SCOPE", [x["outcome"] for x in telemetry["expansion_paths"]])
        self.assertEqual(telemetry["actor_usage"]["cache_read"], 20)
        self.assertIsNone(telemetry["actor_usage"]["cache_creation"])

    def test_package_gate_rejects_wrong_target(self):
        job = self.job(); args = self.inputs(job)
        report = review_package.generate(self.root, generated_root="validation/context", **args,
            before=self.before, after=self.after, changed_paths=self.paths)
        ref = {"path": report["package_path"], "sha256": report["sha256"],
               "target_job_id": "WRONG"}
        review_job = SimpleNamespace(review_mode="DELTA_REVIEW", measurement_mode=True,
            review_package_ref=ref, workspace="ws")
        with patch.object(review_invocation, "observe", return_value=self.manifest), self.assertRaises(ValueError):
            review_invocation.prepare(review_job, self.root,
                {"review_measurement": {"enabled": True, "capabilities": ["CAP"]}},
                {"capability": "CAP"})

    def test_explicit_package_survives_different_review_job_identity(self):
        source_job = self.job(); args = self.inputs(source_job)
        report = review_package.generate(self.root, generated_root="validation/context", **args,
            before=self.before, after=self.after, changed_paths=self.paths)
        # Reproduce E2E-003: a later review job replaces the shared diagnostic
        # Job Context with a different job/instruction identity.
        execution_job = self.job()
        execution_job.job_id = "ARGUS-BOOTSTRAP-CONTEXT-HARNESS-DELTA-REVIEW-E2E-20261003-003"
        execution_job.actor = "claude"; execution_job.mode = "review"
        execution_job.prompt_sha256 = "b" * 64
        replacement, _ = job_context.build(self.root, workspace="ws", capability="CAP",
            job=execution_job, manifest=self.manifest,
            manifest_path="validation/context/CAP/context-manifest.json",
            delta=self.delta, delta_path="validation/context/CAP/delta-report.json",
            evidence_index=self.index,
            evidence_index_path="validation/context/CAP/evidence-index.json",
            declaration=self.declaration)
        write_json(self.dest / "job-context.json", replacement)
        ref = {"path": report["package_path"], "sha256": report["sha256"],
               "target_job_id": "JOB"}
        manifest = review_package.validate_ref(self.root, ref, workspace="ws", capability="CAP",
            current_context_sha256=self.manifest["lifecycle"]["manifest_sha256"],
            current_evidence_index_sha256=review_package.sha256(
                (self.dest / "evidence-index.json").read_bytes()), validate_current_target=True)
        self.assertEqual(manifest["target"]["instruction_sha256"], "a" * 64)

    def test_current_semantic_and_target_freshness_fail_closed(self):
        args = self.inputs(); report = review_package.generate(
            self.root, generated_root="validation/context", **args,
            before=self.before, after=self.after, changed_paths=self.paths)
        ref = {"path": report["package_path"], "sha256": report["sha256"],
               "target_job_id": "JOB"}
        kwargs = dict(workspace="ws", capability="CAP", target_job_id="JOB")
        with self.assertRaisesRegex(ValueError, "Context Manifest"):
            review_package.validate_ref(self.root, ref, current_context_sha256="0" * 64, **kwargs)
        with self.assertRaisesRegex(ValueError, "Evidence Index"):
            review_package.validate_ref(self.root, ref,
                current_evidence_index_sha256="0" * 64, **kwargs)
        (self.root / "implementation.py").write_text("VALUE = 3\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "working tree delta"):
            review_package.validate_ref(self.root, ref, validate_current_target=True, **kwargs)

    def test_source_job_context_internal_hash_mismatch_fails(self):
        args = self.inputs(); report = review_package.generate(
            self.root, generated_root="validation/context", **args,
            before=self.before, after=self.after, changed_paths=self.paths)
        package = self.root / report["package_path"]
        job_path = package / "job-context.json"
        value = json.loads(job_path.read_text("utf-8")); value["job"]["job_id"] = "TAMPER"
        raw = job_context.canonical(value); job_path.write_bytes(raw)
        manifest_path = package / "package-manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        for fact in manifest["files"]:
            if fact["path"] == "job-context.json":
                fact.update(raw_sha256=review_package.sha256(raw), size=len(raw))
        manifest["source_inputs"]["job_context"].update(
            sha256=review_package.sha256(raw), size=len(raw))
        manifest["job_context_sha256"] = review_package.sha256(raw)
        material = dict(manifest); material.pop("manifest_sha256"); material.pop("package_sha256")
        signed = review_package.sha256(review_package.canonical(material))
        manifest.update(manifest_sha256=signed, package_sha256=signed)
        manifest_path.write_bytes(review_package.canonical(manifest))
        ref = {"path": report["package_path"], "sha256": signed, "target_job_id": "JOB"}
        with self.assertRaisesRegex(ValueError, "internal hash mismatch"):
            review_package.validate_ref(self.root, ref, workspace="ws", capability="CAP")

    def test_failure_telemetry_preserves_delta_mode_and_ref(self):
        ref = {"path": "validation/context/CAP/JOB/review-package",
               "sha256": "a" * 64, "target_job_id": "JOB"}
        telemetry = review_invocation.telemetry(
            {"mode": "DELTA_REVIEW", "ref": ref, "prelaunch_failed": True,
             "error": "stale", "current_context_sha256": "c" * 64}, [], "", None)
        self.assertEqual(telemetry["mode"], "DELTA_REVIEW")
        self.assertEqual(telemetry["supplied_package_ref"], ref["path"])
        self.assertEqual(telemetry["stale_reasons"], ["stale"])


if __name__ == "__main__":
    unittest.main()
