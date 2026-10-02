import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import review_evidence
from review_evidence import NORMALIZED_FILES, adopt_review_evidence


class ReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.canonical = self.base / "canonical"
        self.logs = self.base / "logs"
        self.canonical.mkdir()
        self.logs.mkdir()
        (self.canonical / "dirty.txt").write_text("pre-existing", encoding="utf-8")
        for number, name in enumerate(NORMALIZED_FILES):
            (self.logs / name).write_text(f"normalized-{number}\n", encoding="utf-8")
        (self.logs / "review-input.json").write_text(json.dumps({
            "input_manifest_sha256": "b" * 64, "file_count": 7, "files": [],
        }), encoding="utf-8")
        (self.logs / "review-execution.json").write_text(json.dumps({
            "events": [], "final_result_text": "result", "exit_code": 0,
        }), encoding="utf-8")
        (self.logs / "claude-stream.jsonl").write_text("secret transcript\n", encoding="utf-8")
        (self.logs / "stderr.txt").write_text("secret stderr\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def adopt(self, **overrides):
        values = dict(
            canonical=self.canonical,
            review_evidence_root="validation/evidence/external-review",
            job_id="ARGUS-DA-0011",
            log_dir=self.logs,
            actor="claude",
            mode="review",
            canonical_head="a" * 40,
            review_head_before="a" * 40,
            review_head_after="a" * 40,
            input_manifest_sha256="b" * 64,
            file_count=7,
            review_boundary="CLEAN",
            actor_status="DONE",
            evidence_persisted=True,
            adoptable=True,
        )
        values.update(overrides)
        return adopt_review_evidence(**values)

    def destination(self):
        return self.canonical / "validation/evidence/external-review/ARGUS-DA-0011"

    def test_clean_done_persisted_is_adopted_and_raw_is_local_only(self):
        result = self.adopt()
        self.assertEqual(result["status"], "ADOPTED")
        self.assertEqual({item["path"] for item in result["normalized_files"]}, set(NORMALIZED_FILES))
        self.assertEqual({item["path"] for item in result["raw_local_only"]},
                         {"claude-stream.jsonl", "stderr.txt"})
        self.assertTrue(all(item["storage"] == "LOCAL_ONLY" for item in result["raw_local_only"]))
        self.assertFalse((self.destination() / "claude-stream.jsonl").exists())
        self.assertFalse((self.destination() / "stderr.txt").exists())

    def test_deterministic_manifest_serialization_and_hash(self):
        first = self.adopt()
        manifest = (self.destination() / "review-manifest.json").read_bytes()
        self.assertEqual(first["manifest_sha256"], hashlib.sha256(manifest).hexdigest())
        self.assertEqual(first["status"], "ADOPTED")
        second = self.adopt()
        self.assertEqual(second["status"], "NOOP")
        self.assertEqual(second["manifest_sha256"], first["manifest_sha256"])
        self.assertEqual(json.loads(manifest)["adoption"]["mode"], "LIVE")

    def test_nonadoptable_and_not_configured_do_not_write(self):
        for overrides, expected in (({"adoptable": False, "review_boundary": "INPUT_MODIFIED"}, "NOT_RUN"),
                                    ({"review_evidence_root": None}, "NOT_CONFIGURED"),
                                    ({"adoptable": False, "actor_status": "AGENT_ERROR"}, "NOT_RUN"),
                                    ({"adoptable": False, "evidence_persisted": False}, "NOT_RUN")):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.adopt(**overrides)["status"], expected)
                self.assertFalse(self.destination().exists())

    def test_invalid_destinations_rejected(self):
        invalid_roots = ("../escape", "/absolute", "C:\\escape", "\\\\server\\share",
                         "safe:stream", "CON/path", "path/NUL.txt", "trail. /path")
        for root in invalid_roots:
            with self.subTest(root=root):
                result = self.adopt(review_evidence_root=root)
                self.assertEqual(result["status"], "FAILED")
        for job in ("../job", "C:\\job", "job:stream", "AUX"):
            with self.subTest(job=job):
                self.assertEqual(self.adopt(job_id=job)["status"], "FAILED")

    def test_symlink_destination_escape_rejected(self):
        root = self.canonical / "validation"
        outside = self.base / "outside"
        outside.mkdir()
        try:
            root.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable")
        result = self.adopt()
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse((outside / "evidence").exists())

    def test_atomic_staging_uses_replace(self):
        real_replace = os.replace
        calls = []
        def observed(source, destination):
            calls.append((Path(source), Path(destination)))
            self.assertEqual(Path(source).parent, Path(destination).parent)
            return real_replace(source, destination)
        with patch.object(review_evidence.os, "replace", side_effect=observed):
            result = self.adopt()
        self.assertEqual(result["status"], "ADOPTED")
        self.assertEqual(len(calls), 1)

    def test_collision_with_different_package_fails_without_overwrite(self):
        self.assertEqual(self.adopt()["status"], "ADOPTED")
        original = (self.destination() / "review-execution.json").read_bytes()
        (self.logs / "review-execution.json").write_text("different", encoding="utf-8")
        result = self.adopt()
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual((self.destination() / "review-execution.json").read_bytes(), original)

    def test_missing_or_corrupt_required_evidence_fails(self):
        for name, content in (("review-input.json", None),
                              ("review-execution.json", "not-json")):
            with self.subTest(name=name):
                original = (self.logs / name).read_bytes()
                if content is None:
                    (self.logs / name).unlink()
                else:
                    (self.logs / name).write_text(content, encoding="utf-8")
                result = self.adopt()
                self.assertEqual(result["status"], "FAILED")
                self.assertEqual({item["path"] for item in result["raw_local_only"]},
                                 {"claude-stream.jsonl", "stderr.txt"})
                self.assertFalse(self.destination().exists())
                (self.logs / name).write_bytes(original)

    def test_outside_change_during_adoption_fails_and_rolls_back(self):
        original = review_evidence._tree_snapshot
        count = 0
        def mutate(root, excluded):
            nonlocal count
            count += 1
            if count == 2:
                (self.canonical / "unexpected.txt").write_text("changed", encoding="utf-8")
            return original(root, excluded)
        with patch.object(review_evidence, "_tree_snapshot", side_effect=mutate):
            result = self.adopt()
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse(self.destination().exists())

    def test_change_in_sibling_evidence_job_is_outside_allowed_subtree(self):
        original = review_evidence._tree_snapshot
        count = 0
        def mutate(root, excluded):
            nonlocal count
            count += 1
            if count == 2:
                sibling = self.destination().parent / "OTHER-JOB"
                sibling.mkdir(parents=True)
                (sibling / "unexpected.txt").write_text("changed", encoding="utf-8")
            return original(root, excluded)
        with patch.object(review_evidence, "_tree_snapshot", side_effect=mutate):
            result = self.adopt()
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse(self.destination().exists())

    def test_preexisting_dirty_source_is_tolerated_and_untouched(self):
        before = (self.canonical / "dirty.txt").read_bytes()
        self.assertEqual(self.adopt()["status"], "ADOPTED")
        self.assertEqual((self.canonical / "dirty.txt").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
