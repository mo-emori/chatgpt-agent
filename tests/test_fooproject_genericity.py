import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent_worker
import context_harness
import context_trust
from context_activation import legacy_input, prepare, resolve_activation_mode


FIXTURE = Path(__file__).parent / "fixtures" / "foo-project"
CAPABILITY = "FOO-ANALYZER"


class FooProjectGenericityTests(unittest.TestCase):
    def setUp(self):
        Path(".tmp-tests").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=".tmp-tests")
        self.root = Path(self.temp.name).resolve()
        shutil.copytree(FIXTURE, self.root, dirs_exist_ok=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email",
                        "foo@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name",
                        "Foo Fixture"], check=True)
        self.commit("fixture", all_files=True)

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, message, *, all_files=False):
        pathspec = "." if all_files else ".agent/context.json"
        subprocess.run(["git", "-C", str(self.root), "add", pathspec], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", message], check=True)

    def job(self, request):
        prompt = "Analyze the supplied Foo record."
        return SimpleNamespace(
            protocol_version="3", workspace="foo-project", job_id="FOO-JOB-1",
            actor="codex", mode="analyze", prompt=prompt,
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            instruction_ref={"type": "fixture", "context_request": request},
            callback_type=None, callback_url=None)

    def run_pipeline(self, request, cache_name="cache"):
        job = self.job(request)
        result = None
        # First observation creates an initialization candidate; the explicit
        # acceptance is the only operation that establishes trust.
        for attempt in range(2):
            session = context_harness.begin_shadow(
                self.root, workspace=job.workspace, actor=job.actor, mode=job.mode,
                cache_root=self.root / cache_name, job=job)
            self.assertEqual(session["capability"], CAPABILITY)
            result = context_harness.finish_shadow(self.root, session, job_id=job.job_id)
            if attempt == 0:
                record = json.loads(Path(result["trust_state"]["candidate_path"])
                                    .read_text(encoding="utf-8"))
                if record.get("reconciliation_eligible"):
                    context_trust.accept_candidate(
                        root=self.root, cache_root=self.root / cache_name,
                        workspace=job.workspace, capability=CAPABILITY,
                        expected_candidate_sha256=result["manifest_sha256"],
                        operator="foo-test-operator",
                        reason="establish FooProject fixture trust")
        return job, result

    def set_budget(self, budget):
        path = self.root / ".agent/context.json"
        declaration = json.loads(path.read_text(encoding="utf-8"))
        declaration["capabilities"][CAPABILITY]["context"]["max_text_bytes"] = budget
        path.write_text(json.dumps(declaration, indent=2) + "\n", encoding="utf-8")
        self.commit(f"budget {budget}")

    def test_declaration_drives_projection_mapping_selection_and_section_slice(self):
        _, result = self.run_pipeline({
            "context_items": ["input-schema"], "target_files": ["ports/read.foo"]})
        self.assertEqual(result["delta_status"], "NO_IMPACT")
        context = json.loads((self.root / result["job_context"]["job_context_path"])
                             .read_text(encoding="utf-8"))
        refs = context["authoritative_source_refs"]
        self.assertEqual([item["source_ref"] for item in refs],
                         ["context/analyzer-guide.md"])
        self.assertEqual(refs[0]["selected_section_ids"], ["foo-inputs"])
        self.assertEqual([item["source_ref"] for item in context["provenance_source_refs"]],
                         ["reference/source-notes.md"])
        materialized = json.loads(
            (self.root / result["materialized_context"]["materialized_context_path"])
            .read_text(encoding="utf-8"))
        self.assertEqual(materialized["materialization_status"], "READY_BOUNDED")
        self.assertEqual(materialized["raw_provenance_payload_bytes"], 0)
        items = materialized["materialized_items"]
        self.assertEqual([item["section_ids"] for item in items], [["foo-inputs"]])
        payload = base64.b64decode(items[0]["content"])
        self.assertIn(b"Input records", payload)
        self.assertNotIn(b"Apply the declared", payload)
        self.assertNotIn(b"Return a `classification`", payload)

    def test_authority_candidate_remains_blocked_against_foo_trusted_baseline(self):
        request = {"context_items": ["analysis-rules"],
                   "target_files": ["engine/classify.foo"]}
        _, accepted = self.run_pipeline(request, "trust-cache")
        trusted = accepted["trust_state"]["trusted_baseline_sha256"]
        guide = self.root / "context" / "analyzer-guide.md"
        guide.write_text(guide.read_text(encoding="utf-8") + "\nUnaccepted rule.\n",
                         encoding="utf-8")
        for attempt in range(2):
            job = self.job(request)
            session = context_harness.begin_shadow(
                self.root, workspace=job.workspace, actor=job.actor, mode=job.mode,
                cache_root=self.root / "trust-cache", job=job)
            result = context_harness.finish_shadow(
                self.root, session, job_id=f"FOO-BLOCKED-{attempt}")
            self.assertEqual(result["delta_status"], "POTENTIAL_AUTHORITY_CHANGE")
            self.assertFalse(result["trust_state"]["baseline_promoted"])
            self.assertEqual(result["trust_state"]["trusted_baseline_sha256"], trusted)

    def test_exact_budget_is_ready_and_one_byte_small_fails_closed(self):
        request = {"context_items": ["analysis-rules"],
                   "target_files": ["engine/classify.foo"]}
        _, baseline = self.run_pipeline(request, "baseline-cache")
        required = baseline["materialized_context"]["required_payload_bytes"]

        self.set_budget(required)
        _, exact = self.run_pipeline(request, "exact-cache")
        self.assertEqual(exact["materialized_context"]["status"], "READY_BOUNDED")
        exact_value = json.loads(
            (self.root / exact["materialized_context"]["materialized_context_path"])
            .read_text(encoding="utf-8"))
        self.assertEqual(exact_value["measurement"]["required_payload_bytes"], required)
        self.assertTrue(exact_value["materialized_items"])

        self.set_budget(required - 1)
        _, over = self.run_pipeline(request, "over-cache")
        self.assertEqual(over["materialized_context"]["status"], "NEEDS_EXPANSION")
        self.assertFalse(over["trust_state"]["baseline_promoted"])
        self.assertEqual(over["trust_state"]["promotion_reason"],
                         "MATERIALIZED_CONTEXT_NOT_READY")
        trusted, _ = context_harness.load_trusted_baseline(
            self.root / "over-cache", "foo-project", CAPABILITY)
        self.assertIsNone(trusted)
        over_value = json.loads(
            (self.root / over["materialized_context"]["materialized_context_path"])
            .read_text(encoding="utf-8"))
        self.assertEqual(over_value["materialized_items"], [])
        self.assertEqual(over_value["measurement"]["required_payload_bytes"], required)
        self.assertTrue(any(item["reason_code"] == "REQUIRED_CONTEXT_OVER_BUDGET"
                            for item in over_value["expansion_requirements"]))

    def test_activation_modes_and_pre_actor_effective_input_identity(self):
        job, context = self.run_pipeline({
            "context_items": ["output-contract"], "target_files": ["ports/write.foo"]})

        shadow = prepare(job, "SHADOW", context, self.root)
        self.assertEqual(shadow["context_activation_status"], "SHADOW_PREVIEW")
        self.assertNotEqual(shadow["actor_input_sha256"], shadow["effective_input_sha256"])

        allowed = resolve_activation_mode(
            "ENFORCE_AND_INJECT", CAPABILITY, True, frozenset({CAPABILITY}))
        self.assertEqual((allowed.mode, allowed.scope_status),
                         ("ENFORCE_AND_INJECT", "ALLOWLISTED"))
        injected = prepare(job, allowed.mode, context, self.root,
                           configured_mode="ENFORCE_AND_INJECT",
                           scope_status=allowed.scope_status)
        self.assertEqual(injected["context_activation_status"], "INJECTED")
        self.assertEqual(injected["actor_input_sha256"],
                         hashlib.sha256(injected["actor_input"]).hexdigest())
        self.assertEqual(injected["actor_input_sha256"], injected["effective_input_sha256"])

        not_allowed = resolve_activation_mode(
            "ENFORCE_AND_INJECT", CAPABILITY, True, frozenset({"BAR-CAPABILITY"}))
        self.assertEqual((not_allowed.mode, not_allowed.scope_status),
                         ("SHADOW", "NOT_ALLOWLISTED"))
        preview = prepare(job, not_allowed.mode, context, self.root,
                          configured_mode="ENFORCE_AND_INJECT",
                          scope_status=not_allowed.scope_status)
        self.assertEqual(preview["context_activation_status"], "SHADOW_PREVIEW")

    def test_pre_actor_artifacts_are_immutable_across_repeated_post_validation(self):
        job = self.job({"context_items": ["output-contract"],
                        "target_files": ["ports/write.foo"]})
        cache = self.root / "lineage-cache"
        # Establish a trusted baseline, then create the actor-boundary snapshot.
        seed = context_harness.begin_shadow(
            self.root, workspace=job.workspace, actor=job.actor, mode=job.mode,
            cache_root=cache, job=job)
        seed_result = context_harness.finish_shadow(self.root, seed, job_id=job.job_id)
        context_trust.accept_candidate(
            root=self.root, cache_root=cache, workspace=job.workspace,
            capability=CAPABILITY,
            expected_candidate_sha256=seed_result["manifest_sha256"],
            operator="foo-test-operator", reason="establish lineage fixture trust")
        session = context_harness.begin_shadow(
            self.root, workspace=job.workspace, actor=job.actor, mode=job.mode,
            cache_root=cache, job=job)
        pre = context_harness.finish_shadow(self.root, session, job_id=job.job_id)
        session["pre_actor_input"] = pre
        shadow = prepare(job, "SHADOW", pre, self.root)
        activation = prepare(job, "ENFORCE_AND_INJECT", pre, self.root)

        pre_job_path = pre["job_context"]["job_context_path"]
        pre_materialized_path = pre["materialized_context"]["materialized_context_path"]
        pre_job_bytes = (self.root / pre_job_path).read_bytes()
        pre_materialized_bytes = (self.root / pre_materialized_path).read_bytes()
        preflight = dict(activation["preflight"])
        actor_hash = activation["actor_input_sha256"]

        post = context_harness.finish_shadow(self.root, session, job_id=job.job_id)
        post_again = context_harness.finish_shadow(self.root, session, job_id=job.job_id)

        legacy = legacy_input(job)
        self.assertEqual(shadow["actor_input"], legacy)
        self.assertEqual(shadow["actor_input_sha256"], hashlib.sha256(legacy).hexdigest())
        self.assertEqual(post["snapshot"], "POST_ACTOR_VALIDATION")
        self.assertNotEqual(pre_job_path, post["job_context"]["job_context_path"])
        self.assertNotEqual(pre_materialized_path,
                            post["materialized_context"]["materialized_context_path"])
        self.assertNotEqual(pre["materialized_context"]["artifact_identity_sha256"],
                            post["materialized_context"]["artifact_identity_sha256"])
        self.assertEqual((self.root / pre_job_path).read_bytes(), pre_job_bytes)
        self.assertEqual((self.root / pre_materialized_path).read_bytes(),
                         pre_materialized_bytes)
        self.assertEqual(post_again["materialized_context"]["materialized_context_path"],
                         post["materialized_context"]["materialized_context_path"])
        self.assertEqual(activation["preflight"], preflight)
        self.assertEqual(activation["actor_input_sha256"], actor_hash)
        self.assertEqual(actor_hash, activation["effective_input_sha256"])

        activation["actor_started"] = True
        response = agent_worker.build_result(
            job, status="DONE", context=post, context_activation=activation)
        self.assertEqual(response["preflight"], preflight)
        self.assertEqual(response["post_actor_validation"]["materialized_context_path"],
                         post["materialized_context"]["materialized_context_path"])

    def test_over_budget_worker_blocks_before_actor_without_external_process(self):
        request = {"context_items": ["analysis-rules"],
                   "target_files": ["engine/classify.foo"]}
        _, baseline = self.run_pipeline(request, "measure-cache")
        self.set_budget(baseline["materialized_context"]["required_payload_bytes"] - 1)
        job, blocked = self.run_pipeline(request, "blocked-cache")
        published = {}

        with patch.object(agent_worker, "CONTEXT_HARNESS_ACTIVATION_MODE",
                          "ENFORCE_AND_INJECT"), \
             patch.object(agent_worker, "CONTEXT_HARNESS_ENFORCE_CAPABILITIES",
                          frozenset({CAPABILITY})), \
             patch.object(agent_worker, "prepare_execution",
                          return_value=({}, self.root, self.root, {"head": "h"})), \
             patch.object(agent_worker, "begin_shadow",
                          return_value={"capability": CAPABILITY}), \
             patch.object(agent_worker, "finish_shadow", return_value=blocked), \
             patch.object(agent_worker, "run_agent") as run_actor, \
             patch.object(agent_worker.state_store, "mark_completed"), \
             patch.object(agent_worker, "publish_slack_result",
                          side_effect=lambda _log, _say, value: published.update(value)), \
             patch.object(agent_worker, "finalize_browser_callback"), \
             patch.object(agent_worker, "log_job_end"), \
             patch.object(agent_worker, "dispatch_next_queued"):
            agent_worker.execute_job(job, lambda **kwargs: None)

        run_actor.assert_not_called()
        self.assertEqual(published["status"], "BLOCKED_CONTEXT")
        self.assertFalse(published["actor_started"])


if __name__ == "__main__":
    unittest.main()
