import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import context_artifacts


class ContextArtifactCleanupTests(unittest.TestCase):
    def setUp(self):
        Path(".tmp-tests").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=".tmp-tests")
        self.root = Path(self.temp.name).resolve()
        (self.root / ".agent").mkdir()
        (self.root / ".agent/context.json").write_text(json.dumps({
            "schema_version": 1, "mode": "SHADOW",
            "generated_root": "build/context-bundles",
            "capabilities": {"FOO-ANALYZER": {}},
        }), encoding="utf-8")
        self.cap = self.root / "build/context-bundles/FOO-ANALYZER"
        self.cap.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def touch(self, relative, data=b"x"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_dry_run_mutates_nothing(self):
        target = self.touch("build/context-bundles/FOO-ANALYZER/job-context.json")
        before = target.read_bytes()
        plan = context_artifacts.build_plan(self.root)
        self.assertEqual(plan["entries"][0]["action"], "delete")
        self.assertEqual(target.read_bytes(), before)

    def test_apply_deletes_only_replaceable_and_ephemeral(self):
        latest = self.touch("build/context-bundles/FOO-ANALYZER/evidence-index.json")
        temp = self.touch("build/context-bundles/FOO-ANALYZER/write.json.tmp")
        unknown = self.touch("build/context-bundles/FOO-ANALYZER/future.json")
        result = context_artifacts.apply_plan(context_artifacts.build_plan(self.root))
        self.assertFalse(latest.exists())
        self.assertFalse(temp.exists())
        self.assertTrue(unknown.exists())
        self.assertEqual(sum(x["result"] == "deleted" for x in result["results"]), 2)

    def test_trust_critical_source_and_unknown_are_never_selected(self):
        source = self.touch("src/user-data.txt")
        unknown = self.touch("build/context-bundles/FOO-ANALYZER/candidate.json")
        plan = context_artifacts.build_plan(self.root)
        self.assertNotIn(source.as_posix(), [x["path"] for x in plan["entries"]])
        self.assertEqual(next(x for x in plan["entries"] if x["path"].endswith(
            "candidate.json"))["action"], "retain")
        self.assertFalse(any("baseline" in x["path"] or "receipt" in x["path"]
                             for x in plan["entries"] if x["action"] == "delete"))

    def test_job_audit_and_pinned_p0_4_evidence_are_retained(self):
        pinned = self.touch("build/context-bundles/FOO-ANALYZER/"
                            "P0-4-EXTERNAL-SMOKE-20261004-001/context-manifest.json")
        plan = context_artifacts.build_plan(self.root)
        job = next(x for x in plan["entries"] if "P0-4" in x["path"])
        self.assertEqual((job["category"], job["action"]),
                         ("job_audit_evidence", "retain"))
        context_artifacts.apply_plan(plan)
        self.assertTrue(pinned.exists())

    def test_symlink_entry_is_rejected(self):
        outside = self.touch("outside.txt")
        link = self.cap / "job-context.json"
        try:
            os.symlink(outside, link)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        item = context_artifacts.build_plan(self.root)["entries"][0]
        self.assertEqual((item["category"], item["action"]), ("unsafe_path", "reject"))

    def test_generated_root_path_traversal_is_rejected(self):
        declaration = json.loads((self.root / ".agent/context.json").read_text())
        declaration["generated_root"] = "../outside"
        (self.root / ".agent/context.json").write_text(json.dumps(declaration))
        with self.assertRaises(context_artifacts.CleanupError):
            context_artifacts.build_plan(self.root)

    def test_apply_rejects_tampered_plan(self):
        target = self.touch("build/context-bundles/FOO-ANALYZER/job-context.json")
        plan = context_artifacts.build_plan(self.root)
        plan["entries"].append({"path": "src/user-data.txt", "category": "x",
                                "action": "delete", "reason": "x", "bytes": 1})
        with self.assertRaises(context_artifacts.CleanupError):
            context_artifacts.apply_plan(plan)
        self.assertTrue(target.exists())

    def test_apply_skips_file_changed_after_validated_plan(self):
        target = self.touch("build/context-bundles/FOO-ANALYZER/job-context.json")
        plan = context_artifacts.build_plan(self.root)
        target.write_bytes(b"changed-size")
        result = context_artifacts.apply_plan(plan)
        self.assertEqual(result["results"][0]["result"], "error")
        self.assertTrue(target.exists())

    def test_foo_project_generic_workspace_cli_defaults_to_dry_run(self):
        target = self.touch("build/context-bundles/FOO-ANALYZER/"
                            "materialized-context-post-actor-validation.json")
        stream = StringIO()
        with redirect_stdout(stream):
            code = context_artifacts.main(["cleanup", "--workspace", str(self.root)])
        output = json.loads(stream.getvalue())
        self.assertEqual((code, output["mode"]), (0, "dry-run"))
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
