import datetime as dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import context_harness as harness
import context_trust


class ContextTrustAcceptanceTests(unittest.TestCase):
    def setUp(self):
        Path(".tmp-tests").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=".tmp-tests")
        self.root = Path(self.temp.name).resolve()
        self.cache = self.root / "cache"
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email",
                        "foo@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Foo"], check=True)
        (self.root / ".agent").mkdir()
        (self.root / "authority.txt").write_text("A\n", encoding="utf-8")
        declaration = {"schema_version": 1, "mode": "SHADOW",
            "capabilities": {"FOO-CAP": {"selectors": {
                "actors": ["codex"], "modes": ["implementation"]}, "sources": [{
                    "path": "authority.txt", "kind": "contract", "required": True,
                    "authority": "authoritative", "context_items": ["rules"]}]}}}
        (self.root / ".agent/context.json").write_text(
            json.dumps(declaration), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "A"], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def candidate(self, job="JOB"):
        session = harness.begin_shadow(
            self.root, workspace="foo-project", actor="codex", mode="implementation",
            cache_root=self.cache)
        return harness.finish_shadow(self.root, session, job_id=job)

    def accept(self, result, **overrides):
        values = dict(root=self.root, cache_root=self.cache, workspace="foo-project",
                      capability="FOO-CAP",
                      expected_candidate_sha256=result["manifest_sha256"],
                      operator="human@example.invalid", reason="reviewed Foo authority",
                      now=dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc))
        values.update(overrides)
        return context_trust.accept_candidate(**values)

    def establish_a(self):
        initial = self.candidate("INIT-A")
        self.assertFalse(initial["trust_state"]["baseline_promoted"])
        accepted = self.accept(initial, reason="explicit initial Foo trust")
        return initial["manifest_sha256"], accepted

    def test_acceptance_lifecycle_archive_audit_and_unchanged_operation(self):
        a_hash, _ = self.establish_a()
        (self.root / "authority.txt").write_text("B\n", encoding="utf-8")
        blocked = self.candidate("BLOCK-B")
        self.assertEqual(blocked["delta_status"], "POTENTIAL_AUTHORITY_CHANGE")
        b_hash = blocked["manifest_sha256"]
        accepted = self.accept(blocked, expected_current_trusted_sha256=a_hash)
        self.assertEqual(accepted["status"], "ACCEPTED")
        self.assertEqual(accepted["trusted_manifest_sha256"], b_hash)
        archive = self.cache / accepted["archive_path"]
        archived_bytes = archive.read_bytes()
        receipt = json.loads((self.cache / accepted["receipt_path"]).read_text("utf-8"))
        self.assertEqual(receipt["operator"], "human@example.invalid")
        self.assertEqual(receipt["reason"], "reviewed Foo authority")
        self.assertEqual(receipt["accepted_at"], "2026-01-02T03:04:05Z")
        self.assertEqual(receipt["trust_domain"]["workspace"], "foo-project")
        self.assertEqual(receipt["previous_trusted"]["manifest_sha256"], a_hash)
        self.assertEqual(receipt["accepted_candidate"]["manifest_sha256"], b_hash)
        trusted, errors = harness.load_trusted_baseline(
            self.cache, "foo-project", "FOO-CAP")
        self.assertFalse(errors)
        self.assertEqual(trusted["lifecycle"]["manifest_sha256"], b_hash)
        unchanged = self.candidate("UNCHANGED-B")
        self.assertEqual(unchanged["delta_status"], "NO_IMPACT")
        self.assertEqual(archive.read_bytes(), archived_bytes)

    def test_wrong_hash_and_cas_mismatch_reject(self):
        a_hash, _ = self.establish_a()
        (self.root / "authority.txt").write_text("B\n", encoding="utf-8")
        blocked = self.candidate("B")
        with self.assertRaisesRegex(context_trust.AcceptanceError, "expected candidate"):
            self.accept(blocked, expected_candidate_sha256="0" * 64)
        with self.assertRaisesRegex(context_trust.AcceptanceError, "expected current"):
            self.accept(blocked, expected_current_trusted_sha256="f" * 64)
        trusted, _ = harness.load_trusted_baseline(self.cache, "foo-project", "FOO-CAP")
        self.assertEqual(trusted["lifecycle"]["manifest_sha256"], a_hash)

    def test_stale_candidate_and_wrong_domain_reject(self):
        self.establish_a()
        (self.root / "authority.txt").write_text("B\n", encoding="utf-8")
        blocked = self.candidate("B")
        (self.root / "authority.txt").write_text("C\n", encoding="utf-8")
        with self.assertRaisesRegex(context_trust.AcceptanceError, "stale"):
            self.accept(blocked)
        with self.assertRaisesRegex(context_trust.AcceptanceError, "JSON"):
            self.accept(blocked, workspace="wrong-workspace")

    def test_repeated_observation_never_promotes_and_absence_stays_untrusted(self):
        first = self.candidate("FIRST")
        for index in range(3):
            repeated = self.candidate(f"REPEAT-{index}")
            self.assertFalse(repeated["trust_state"]["baseline_promoted"])
        baseline, errors = harness.load_trusted_baseline(
            self.cache, "foo-project", "FOO-CAP")
        self.assertIsNone(baseline)
        self.assertFalse(errors)
        self.assertEqual(first["manifest_sha256"], repeated["manifest_sha256"])

    def test_legacy_baseline_is_unproven_but_can_be_explicitly_migrated(self):
        legacy = harness.observe(self.root, "FOO-CAP")
        harness._atomic_write(harness._baseline_path(
            self.cache, "foo-project", "FOO-CAP"), legacy)
        trusted, errors = harness.load_trusted_baseline(
            self.cache, "foo-project", "FOO-CAP")
        self.assertIsNone(trusted)
        self.assertIn("legacy baseline", errors[0])
        migration = self.candidate("MIGRATE")
        self.assertEqual(migration["delta_status"], "POTENTIAL_AUTHORITY_CHANGE")
        self.assertTrue(migration["would_block"])
        result = self.accept(migration, expected_current_trusted_sha256=
                             legacy["lifecycle"]["manifest_sha256"],
                             reason="explicitly migrate reviewed legacy Foo baseline")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertIsNotNone(result["archive_path"])

    def test_receipt_tampering_fails_closed(self):
        _, accepted = self.establish_a()
        receipt_path = self.cache / accepted["receipt_path"]
        receipt = json.loads(receipt_path.read_text("utf-8"))
        receipt["reason"] = "tampered"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        trusted, errors = harness.load_trusted_baseline(
            self.cache, "foo-project", "FOO-CAP")
        self.assertIsNone(trusted)
        self.assertIn("receipt hash mismatch", errors[0])

    def test_repeated_acceptance_is_deterministically_rejected(self):
        initial = self.candidate("INIT")
        self.accept(initial)
        with self.assertRaisesRegex(context_trust.AcceptanceError,
                                    "no longer matches current trusted baseline"):
            self.accept(initial)


if __name__ == "__main__":
    unittest.main()
