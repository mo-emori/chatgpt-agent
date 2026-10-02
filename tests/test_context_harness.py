import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import context_harness as harness


class ContextHarnessTests(unittest.TestCase):
    def setUp(self):
        Path(".tmp-tests").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=".tmp-tests")
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / ".agent").mkdir()
        (self.root / "contract.txt").write_bytes(b"one\ntwo\n")
        (self.root / "unrelated.txt").write_text("outside graph", encoding="utf-8")
        self.declaration = {
            "schema_version": 1, "mode": "SHADOW",
            "generated_root": "validation/context",
            "capabilities": {"CAP": {
                "label": "PoC", "selectors": {"actors": ["codex"], "modes": ["implementation"]},
                "sources": [
                    {"path": "contract.txt", "kind": "contract", "required": True,
                     "authority": "authoritative", "context_items": ["rules"]},
                    {"glob": "evidence/*.json", "kind": "evidence", "required": False,
                     "authority": "non_authority", "context_items": ["proof"]},
                ],
            }},
        }
        self.write_declaration()
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "base"], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_declaration(self):
        (self.root / ".agent/context.json").write_text(
            json.dumps(self.declaration, sort_keys=True), encoding="utf-8")

    def observe(self):
        return harness.observe(self.root, "CAP")

    def test_deterministic_identical_and_unrelated_dirty_is_no_impact(self):
        first = self.observe(); second = self.observe()
        self.assertEqual(first["lifecycle"]["manifest_sha256"], second["lifecycle"]["manifest_sha256"])
        (self.root / "unrelated.txt").write_text("dirty but irrelevant", encoding="utf-8")
        third = self.observe()
        self.assertEqual(harness.scan(second, third)["delta_status"], "NO_IMPACT")

    def test_dirty_authority_and_eol_only_are_authority_changes(self):
        before = self.observe()
        (self.root / "contract.txt").write_bytes(b"one\r\ntwo\r\n")
        after = self.observe(); delta = harness.scan(before, after)
        self.assertEqual(delta["delta_status"], "POTENTIAL_AUTHORITY_CHANGE")
        self.assertEqual(delta["changed_sources"][0]["change_kind"], "EOL_ONLY")
        self.assertIn(" M contract.txt", after["observed"]["sources"][0]["git_status"])

    def test_non_authority_evidence_addition_is_context_update(self):
        before = self.observe()
        (self.root / "evidence").mkdir()
        (self.root / "evidence/new.json").write_text("{}", encoding="utf-8")
        delta = harness.scan(before, self.observe())
        self.assertEqual(delta["delta_status"], "CONTEXT_UPDATE")
        self.assertEqual(delta["changed_sources"][0]["change_kind"], "ADDED")
        self.assertTrue(delta["changed_sources"][0]["source"].endswith("new.json"))

    def test_missing_required_is_unverifiable(self):
        before = self.observe()
        (self.root / "contract.txt").unlink()
        delta = harness.scan(before, self.observe())
        self.assertEqual(delta["delta_status"], "UNVERIFIABLE")
        self.assertTrue(delta["would_block"])

    def test_untracked_declared_authority_is_observed(self):
        self.declaration["capabilities"]["CAP"]["sources"].append(
            {"path": "new-contract.txt", "kind": "contract", "required": True,
             "authority": "authoritative", "context_items": ["new-rules"]})
        self.write_declaration()
        (self.root / "new-contract.txt").write_text("v1", encoding="utf-8")
        before = self.observe()
        fact = next(x for x in before["observed"]["sources"] if x["path"] == "new-contract.txt")
        self.assertTrue(fact["git_status"].startswith("??"))
        (self.root / "new-contract.txt").write_text("v2", encoding="utf-8")
        self.assertEqual(harness.scan(before, self.observe())["delta_status"],
                         "POTENTIAL_AUTHORITY_CHANGE")

    def test_declaration_dependency_change_is_authority_change(self):
        before = self.observe()
        self.declaration["capabilities"]["CAP"]["sources"][0]["context_items"] = ["other"]
        self.write_declaration()
        delta = harness.scan(before, self.observe())
        self.assertEqual(delta["delta_status"], "POTENTIAL_AUTHORITY_CHANGE")
        self.assertIn("DEPENDENCY_EDGES_CHANGED", delta["reasons"])

    def test_actor_cannot_declare_approved_semantics(self):
        self.declaration["approved_semantics"] = {"rule": "actor says approved"}
        self.write_declaration()
        declaration, _, errors = harness.load_declaration(self.root)
        self.assertIsNone(declaration)
        self.assertTrue(errors)

    def test_shadow_evidence_and_result_fields_do_not_enforce(self):
        cache = self.root / "cache"
        session = harness.begin_shadow(self.root, workspace="fixture", actor="codex",
                                       mode="implementation", cache_root=cache)
        (self.root / "contract.txt").write_text("changed", encoding="utf-8")
        result = harness.finish_shadow(self.root, session, job_id="JOB-1")
        self.assertEqual(result["delta_status"], "POTENTIAL_AUTHORITY_CHANGE")
        self.assertTrue(result["would_block"])
        self.assertTrue((self.root / result["report_path"]).is_file())
        self.assertEqual(session["pre"]["approved_semantics"], {})


if __name__ == "__main__":
    unittest.main()
