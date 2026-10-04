import datetime as dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def migrate(self, result, legacy_hash, **overrides):
        values = dict(root=self.root, cache_root=self.cache, workspace="foo-project",
                      capability="FOO-CAP",
                      expected_candidate_sha256=result["manifest_sha256"],
                      expected_legacy_trusted_sha256=legacy_hash,
                      operator="migration-operator@example.invalid",
                      reason="v0.1 legacy trust migration",
                      now=dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc))
        values.update(overrides)
        return context_trust.legacy_auto_migrate(**values)

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
        self.assertEqual(receipt["acceptance_type"], "HUMAN_EXPLICIT")
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

    def test_legacy_baseline_valid_candidate_auto_migrates_with_distinct_receipt(self):
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
        legacy_hash = legacy["lifecycle"]["manifest_sha256"]
        with self.assertRaisesRegex(context_trust.AcceptanceError,
                                    "legacy-auto-migrate"):
            self.accept(migration, expected_current_trusted_sha256=legacy_hash)
        result = self.migrate(migration, legacy_hash)
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertIsNotNone(result["archive_path"])
        archive = self.cache / result["archive_path"]
        self.assertEqual(archive.read_bytes(), harness._canonical(legacy))
        receipt = json.loads((self.cache / result["receipt_path"]).read_text("utf-8"))
        self.assertEqual(receipt["acceptance_type"], "LEGACY_AUTO_MIGRATION")
        self.assertEqual(receipt["migration"]["schema_version"], 1)
        self.assertFalse(receipt["migration"][
            "historical_provenance_individually_revalidated"])
        self.assertEqual(receipt["migration"]["declaration_sha256"],
                         legacy["observed"]["declaration"]["raw_sha256"])
        self.assertTrue(receipt["migration"]["source_identities"])

        with self.assertRaisesRegex(context_trust.AcceptanceError,
                                    "new-format baseline already exists"):
            self.migrate(migration, legacy_hash)

        archived_bytes = archive.read_bytes()
        (self.root / "authority.txt").write_text("POST-MIGRATION\n", encoding="utf-8")
        blocked = self.candidate("POST-MIGRATION")
        self.assertEqual(blocked["delta_status"], "POTENTIAL_AUTHORITY_CHANGE")
        self.assertTrue(blocked["would_block"])
        self.assertEqual(archive.read_bytes(), archived_bytes)

    def test_legacy_migration_rejects_wrong_hash_stale_cas_domain_and_no_candidate(self):
        legacy = harness.observe(self.root, "FOO-CAP")
        legacy_hash = legacy["lifecycle"]["manifest_sha256"]
        harness._atomic_write(harness._baseline_path(
            self.cache, "foo-project", "FOO-CAP"), legacy)
        migration = self.candidate("MIGRATE-REJECTIONS")
        with self.assertRaisesRegex(context_trust.AcceptanceError, "expected candidate"):
            self.migrate(migration, legacy_hash, expected_candidate_sha256="0" * 64)
        with self.assertRaisesRegex(context_trust.AcceptanceError, "expected legacy"):
            self.migrate(migration, "f" * 64)
        other_cache = self.root / "other-cache"
        harness._atomic_write(harness._baseline_path(
            other_cache, "other-project", "FOO-CAP"), legacy)
        source_candidate = harness._candidate_path(
            self.cache, "foo-project", "FOO-CAP")
        target_candidate = harness._candidate_path(
            other_cache, "other-project", "FOO-CAP")
        target_candidate.parent.mkdir(parents=True, exist_ok=True)
        target_candidate.write_bytes(source_candidate.read_bytes())
        with self.assertRaisesRegex(context_trust.AcceptanceError, "trust domain mismatch"):
            self.migrate(migration, legacy_hash, cache_root=other_cache,
                         workspace="other-project")
        (self.root / "authority.txt").write_text("STALE\n", encoding="utf-8")
        with self.assertRaisesRegex(context_trust.AcceptanceError, "stale"):
            self.migrate(migration, legacy_hash)

        empty_cache = self.root / "empty-cache"
        harness._atomic_write(harness._baseline_path(
            empty_cache, "foo-project", "FOO-CAP"), legacy)
        with self.assertRaisesRegex(context_trust.AcceptanceError, "JSON"):
            context_trust.legacy_auto_migrate(
                root=self.root, cache_root=empty_cache, workspace="foo-project",
                capability="FOO-CAP", expected_candidate_sha256="0" * 64,
                expected_legacy_trusted_sha256=legacy_hash, operator="operator",
                reason="v0.1 legacy trust migration")

    def test_new_format_baseline_rejects_legacy_migration(self):
        initial = self.candidate("NEW-FORMAT")
        accepted = self.accept(initial)
        with self.assertRaisesRegex(context_trust.AcceptanceError,
                                    "new-format baseline already exists"):
            self.migrate(initial, accepted["trusted_manifest_sha256"])

    def test_interruption_after_receipt_is_recoverable(self):
        legacy = harness.observe(self.root, "FOO-CAP")
        legacy_hash = legacy["lifecycle"]["manifest_sha256"]
        baseline_path = harness._baseline_path(self.cache, "foo-project", "FOO-CAP")
        harness._atomic_write(baseline_path, legacy)
        migration = self.candidate("INTERRUPTED")
        real_atomic_write = harness._atomic_write

        def interrupt_promotion(path, value):
            if Path(path) == baseline_path:
                raise OSError("simulated interruption")
            return real_atomic_write(path, value)

        with patch.object(harness, "_atomic_write", side_effect=interrupt_promotion), \
             self.assertRaisesRegex(OSError, "simulated interruption"):
            self.migrate(migration, legacy_hash)
        self.assertEqual(json.loads(baseline_path.read_text("utf-8"))["lifecycle"].get(
            "acceptance_provenance"), None)

        recovered = self.migrate(migration, legacy_hash)
        self.assertEqual(recovered["status"], "ACCEPTED")
        trusted, errors = harness.load_trusted_baseline(
            self.cache, "foo-project", "FOO-CAP")
        self.assertFalse(errors)
        self.assertEqual(trusted["lifecycle"]["manifest_sha256"],
                         migration["manifest_sha256"])

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
