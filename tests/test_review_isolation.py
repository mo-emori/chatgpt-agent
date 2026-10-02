import json
import subprocess
import tempfile
import unittest
import os
import stat
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_worker
from actors import claude, codex
from actors.claude_stream import parse_stream_json
from actors.environment import build_actor_env
from actors.review_workspace import ReviewPreparationError, build_input_manifest, cleanup_review, create_review_workspace, finish_review, scan_outputs
from actors.process_runner import ProcessResult, ProcessTimeoutExpired, run_process
from actors.process_tree import ProcessTree


class ActorEnvironmentTests(unittest.TestCase):
    def test_worker_secrets_are_absent_for_both_actors(self):
        source = {"PATH": "bin", "SLACK_BOT_TOKEN": "secret", "SLACK_APP_TOKEN": "secret",
                  "NOTION_WORKER_TOKEN": "secret", "GOOGLE_APPLICATION_CREDENTIALS": "g.json",
                  "OPENAI_API_KEY": "openai", "ANTHROPIC_API_KEY": "anthropic"}
        codex = build_actor_env("codex", source); claude = build_actor_env("claude", source)
        for env in (codex, claude):
            self.assertFalse({"SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "NOTION_WORKER_TOKEN"} & env.keys())
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", codex)
        self.assertEqual(codex["OPENAI_API_KEY"], "openai")
        self.assertEqual(claude["ANTHROPIC_API_KEY"], "anthropic")
        self.assertEqual(claude["PYTEST_ADDOPTS"], "-p no:cacheprovider")


class ManifestAndCloneTests(unittest.TestCase):
    def git(self, root, *args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)

    def make_repo(self, root):
        self.git(root, "init"); self.git(root, "config", "user.email", "test@example.invalid")
        self.git(root, "config", "user.name", "Test")
        (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        (root / "tracked.txt").write_bytes(b"original\r\nbytes")
        self.git(root, "add", "."); self.git(root, "commit", "-m", "base")

    def create_review(self, root):
        try:
            return create_review_workspace(root, "TEST")
        except ReviewPreparationError as exc:
            if "NtCreateDirectoryObject" in str(exc) or "Could not read from remote repository" in str(exc):
                self.skipTest("Git for Windows local clone is blocked by the test sandbox")
            raise

    def test_manifest_deterministic_and_untracked_raw_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make_repo(root); (root / "z.bin").write_bytes(b"\x00\xff")
            self.assertEqual(build_input_manifest(root), build_input_manifest(root))
            self.assertEqual([x["path"] for x in build_input_manifest(root)["files"]],
                             [".gitignore", "tracked.txt", "z.bin"])

    def test_deleted_reconciliation_and_ignored_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make_repo(root); (root / "tracked.txt").unlink()
            review = self.create_review(root)
            try:
                self.assertFalse((review.root / "tracked.txt").exists())
                output = review.root / "ignored" / "result.log"; output.parent.mkdir(); output.write_text("x")
                self.assertIn("ignored/result.log", scan_outputs(review))
                self.assertEqual(finish_review(review)[0], "CLEAN")
            finally: cleanup_review(review)

    def test_worker_settings_explicitly_approve_bash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make_repo(root); review = self.create_review(root)
            try:
                settings = json.loads(review.settings_path.read_text(encoding="utf-8"))
                self.assertEqual(settings["hooks"], {})
                self.assertEqual(settings["permissions"]["allow"], ["Bash"])
            finally: cleanup_review(review)

    def test_restoration_clean_then_final_mutation_violation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make_repo(root); review = self.create_review(root)
            try:
                target = review.root / "tracked.txt"; original = target.read_bytes()
                target.write_text("mutant"); target.write_bytes(original)
                self.assertEqual(finish_review(review)[0], "CLEAN")
                target.write_text("changed")
                self.assertEqual(finish_review(review)[0], "INPUT_MODIFIED")
            finally: cleanup_review(review)

    def test_canonical_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make_repo(root); review = self.create_review(root)
            try:
                (root / "tracked.txt").write_text("external")
                self.assertEqual(finish_review(review)[0], "CANONICAL_STATE_CHANGED")
            finally: cleanup_review(review)


class StreamCompatibilityTests(unittest.TestCase):
    def test_result_and_tool_evidence(self):
        final = '<AGENT_RESULT>\n{"summary":"ok","artifacts":[]}\n</AGENT_RESULT>'
        lines = [{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"pytest"}}]}},
                 {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1"}]}},
                 {"type":"result","subtype":"success","is_error":False,"result":final}]
        text, events = parse_stream_json("\n".join(json.dumps(x) for x in lines))
        self.assertEqual(text, final)
        self.assertEqual([x["kind"] for x in events], ["tool_use", "tool_result", "result"])
        self.assertEqual(events[0]["command"], "pytest")


class LauncherContractTests(unittest.TestCase):
    @patch.object(claude, "run_process")
    @patch.object(claude, "validate_workspace")
    def test_claude_restricted_stream_launcher_contract(self, validate, run_process):
        job = SimpleNamespace(workspace="local-agent", prompt="review", actor="claude")
        claude.run(job, workdir=Path("review"), settings_path=Path("managed.json"))
        args = run_process.call_args.kwargs["args"]
        self.assertEqual(args[0:2], [claude.CLAUDE_CMD, "-p"])
        for required in ("--restricted", "--tools", "Read,Glob,Grep,Bash", "--settings",
                         "--safe-mode", "--strict-mcp-config", "--permission-mode",
                         "dontAsk", "--permission-prompts", "none", "--no-session-persistence",
                         "--output-format", "stream-json", "--verbose"):
            self.assertIn(required, args)
        self.assertFalse({"Edit", "Write", "PowerShell"} & set(args))

    @patch.object(claude, "run_process")
    @patch.object(claude, "validate_workspace")
    def test_claude_requires_managed_settings(self, validate, run_process):
        job = SimpleNamespace(workspace="local-agent", prompt="review", actor="claude")
        with self.assertRaisesRegex(ValueError, "settings_path"):
            claude.run(job, workdir=Path("review"), settings_path=None)
        run_process.assert_not_called()

    @patch.object(codex, "run_process")
    @patch.object(codex, "validate_workspace")
    def test_codex_workspace_write_contract_has_mxc_invariant(self, validate, run_process):
        job = SimpleNamespace(workspace="local-agent", prompt="implement", actor="codex")
        codex.run(job)
        args = run_process.call_args.kwargs["args"]
        self.assertEqual(args, [
            codex.CODEX_CMD, "exec", "-c", codex.CODEX_SANDBOX_OVERRIDE,
            "--sandbox", "workspace-write", "-",
        ])


class CleanupAndProcessTreeTests(unittest.TestCase):
    def test_cleanup_clears_readonly_files(self):
        base = Path(tempfile.mkdtemp())
        root = base / "workspace"; root.mkdir()
        locked = root / "readonly.obj"; locked.write_text("x", encoding="utf-8")
        locked.chmod(stat.S_IREAD)
        review = SimpleNamespace(root=root)
        self.assertEqual(cleanup_review(review), "DONE")
        self.assertFalse(base.exists())

    def test_process_tree_abstraction_is_pid_scoped(self):
        tree = ProcessTree()
        process = Mock(pid=1234)
        process.poll.return_value = None
        with patch("actors.process_tree.os.name", "nt"), patch.object(tree, "_job", None):
            tree.terminate(process)
        process.kill.assert_called_once_with()


class ProcessRunnerOwnershipTests(unittest.TestCase):
    def fake_process(self, *, stdin=None, wait_effect=None):
        process = Mock(pid=4321, returncode=None)
        process.stdout = io.StringIO("partial stdout\n")
        process.stderr = io.StringIO("partial stderr\n")
        process.stdin = stdin or Mock()
        process.poll.side_effect = lambda: process.returncode

        effects = list(wait_effect or [])
        def wait(timeout=None):
            if effects:
                effect = effects.pop(0)
                if isinstance(effect, BaseException):
                    raise effect
            process.returncode = -1
            return process.returncode
        process.wait.side_effect = wait
        return process

    def run_failure(self, process, *, mark_error=None):
        tree = Mock()
        self.last_tree = tree
        tree.popen_kwargs.return_value = {}
        patches = [
            patch("actors.process_runner.subprocess.Popen", return_value=process),
            patch("actors.process_runner.ProcessTree", return_value=tree),
            patch("actors.process_runner.build_actor_env", return_value={}),
            patch("actors.process_runner.state_store.mark_running", side_effect=mark_error),
        ]
        started = [item.start() for item in patches]
        try:
            run_process(job=SimpleNamespace(job_id="J", actor="claude"), args=["actor"],
                        cwd=Path("."), timeout=1, input_text="prompt")
        finally:
            for item in reversed(patches): item.stop()
        return tree

    def test_mark_running_failure_settles_owned_tree_and_closes_handle(self):
        process = self.fake_process()
        with self.assertRaisesRegex(OSError, "state failed"):
            self.run_failure(process, mark_error=OSError("state failed"))
        self.last_tree.terminate.assert_called_once_with(process)
        self.last_tree.close.assert_called_once_with()

    def test_stdin_write_and_close_failures_settle_owned_tree(self):
        for operation in ("write", "close"):
            with self.subTest(operation=operation):
                stdin = Mock()
                getattr(stdin, operation).side_effect = OSError(f"{operation} failed")
                process = self.fake_process(stdin=stdin)
                tree = Mock(); tree.popen_kwargs.return_value = {}
                with patch("actors.process_runner.subprocess.Popen", return_value=process), \
                     patch("actors.process_runner.ProcessTree", return_value=tree), \
                     patch("actors.process_runner.build_actor_env", return_value={}), \
                     patch("actors.process_runner.state_store.mark_running"):
                    with self.assertRaisesRegex(OSError, f"{operation} failed"):
                        run_process(job=SimpleNamespace(job_id="J", actor="claude"),
                                    args=["actor"], cwd=Path("."), timeout=1,
                                    input_text="prompt")
                tree.terminate.assert_called_once_with(process)
                tree.close.assert_called_once_with()

    def test_timeout_carries_partial_output_after_owned_tree_cleanup(self):
        timeout = subprocess.TimeoutExpired(["actor"], 1)
        process = self.fake_process(wait_effect=[timeout])
        tree = Mock(); tree.popen_kwargs.return_value = {}
        with patch("actors.process_runner.subprocess.Popen", return_value=process), \
             patch("actors.process_runner.ProcessTree", return_value=tree), \
             patch("actors.process_runner.build_actor_env", return_value={}), \
             patch("actors.process_runner.state_store.mark_running"):
            with self.assertRaises(ProcessTimeoutExpired) as caught:
                run_process(job=SimpleNamespace(job_id="J", actor="claude"),
                            args=["actor"], cwd=Path("."), timeout=1)
        self.assertEqual(caught.exception.result.stdout, "partial stdout\n")
        self.assertEqual(caught.exception.result.stderr, "partial stderr\n")
        tree.terminate.assert_called_once_with(process)
        tree.close.assert_called_once_with()


class ReviewTerminalConvergenceTests(unittest.TestCase):
    def job(self):
        return SimpleNamespace(protocol_version="1", job_id="REVIEW-1", actor="claude",
                               mode="review", workspace="local-agent", prompt="review",
                               prompt_sha256="a" * 64, callback_type="chatgpt_browser",
                               callback_url="https://example.invalid/callback")

    def review(self, root):
        return SimpleNamespace(
            root=root / "clone", settings_path=root / "settings.json",
            canonical_before={"head": "abc"}, head_before="abc",
            input_manifest={"input_manifest_sha256": "hash", "file_count": 1},
            canonical_diff_stat="", review_diff_stat="",
        )

    def run_case(self, boundary="CLEAN", parse_error=None, boundary_error=None,
                 state_error=None, callback_effect=None, callback_type="chatgpt_browser"):
        job = self.job(); say = Mock()
        job.callback_type = callback_type
        temp = tempfile.TemporaryDirectory(); root = Path(temp.name)
        review = self.review(root); review.root.mkdir(); review.settings_path.write_text("{}")
        final = '<AGENT_RESULT>\n{"summary":"CHANGES REQUESTED","artifacts":[]}\n</AGENT_RESULT>'
        raw = json.dumps({"type":"result", "result":final})
        patches = [
            patch.object(agent_worker, "prepare_execution", return_value=({"artifact_roots": []}, root, root, {})),
            patch.object(agent_worker, "create_review_workspace", return_value=review),
            patch.object(agent_worker.claude, "run", return_value=ProcessResult(0, raw, "")),
            patch.object(agent_worker, "finish_review", return_value=(boundary, {}, "abc", []),
                         side_effect=boundary_error),
            patch.object(agent_worker, "diff_head_stat", return_value=""),
            patch.object(agent_worker, "cleanup_review", return_value="DONE"),
            patch.object(agent_worker.state_store, "mark_completed", side_effect=state_error),
            patch.object(agent_worker, "finalize_browser_callback",
                         side_effect=callback_effect),
            patch.object(agent_worker, "dispatch_next_queued"),
            patch.object(agent_worker, "log_job_end"),
            patch.object(agent_worker, "process_artifacts", return_value=("CHANGES REQUESTED", "DONE", [], [])),
        ]
        if parse_error:
            patches.append(patch.object(agent_worker, "parse_stream_json", side_effect=parse_error))
        started = [p.start() for p in patches]
        try:
            agent_worker.execute_claude_review(job, say)
            response = json.loads((root / "result.json").read_text(encoding="utf-8"))
            return response, say, started
        finally:
            for p in reversed(patches): p.stop()
            temp.cleanup()

    def test_clean_done_is_adoptable_despite_changes_requested_prose(self):
        response, say, mocks = self.run_case("CLEAN")
        self.assertEqual(response["status"], "DONE")
        self.assertTrue(response["review_execution"]["adoptable"])
        self.assertEqual(response["review_evidence"]["status"], "NOT_CONFIGURED")
        self.assertEqual(say.call_count, 1)
        mocks[-2].assert_called_once()

    def test_job_end_once_for_each_callback_outcome_and_no_callback(self):
        def callback_outcome(callback_status, error=None):
            def apply(_job, _log_dir, response, **_kwargs):
                response["callback"] = {"status": callback_status}
                if error is not None:
                    response["callback"]["error"] = error
            return apply

        cases = (
            ("confirmed", callback_outcome("DONE"), "chatgpt_browser", "DONE"),
            ("unknown", callback_outcome(
                "FAILED", "DELIVERY_UNKNOWN: DELIVERY_ACK_TIMEOUT"
            ), "chatgpt_browser", "FAILED"),
            ("exception", RuntimeError("callback exploded"),
             "chatgpt_browser", None),
            ("no callback", callback_outcome("SKIPPED"), None, "SKIPPED"),
        )
        for name, effect, callback_type, callback_status in cases:
            with self.subTest(name=name):
                response, _, mocks = self.run_case(
                    callback_effect=effect, callback_type=callback_type
                )
                self.assertEqual(response["status"], "DONE")
                self.assertEqual(response["review_boundary"]["status"], "CLEAN")
                self.assertEqual(response["cleanup_status"], "DONE")
                if callback_status is not None:
                    self.assertEqual(response["callback"]["status"], callback_status)
                mocks[-2].assert_called_once()

    def test_terminal_slack_has_final_adoption_status_and_manifest_sha(self):
        adopted = {"status": "ADOPTED", "mode": "LIVE", "destination": "evidence/J",
                   "manifest_sha256": "f" * 64, "normalized_files": [],
                   "raw_local_only": []}
        with patch.object(agent_worker, "adopt_review_evidence", return_value=adopted) as adoption, \
             patch.dict(agent_worker.WORKSPACES["local-agent"],
                        {"review_evidence_root": "evidence"}):
            response, say, mocks = self.run_case("CLEAN")
        self.assertEqual(response["status"], "DONE")
        self.assertEqual(response["review_boundary"]["status"], "CLEAN")
        self.assertTrue(response["review_execution"]["adoptable"])
        self.assertEqual(response["review_evidence"]["status"], "ADOPTED")
        published = json.loads(say.call_args.args[0].removeprefix("```json\n").removesuffix("\n```"))
        self.assertEqual(published["review_evidence"], adopted)
        adoption.assert_called_once()

    def test_adoption_exception_does_not_rewrite_successful_review(self):
        with patch.object(agent_worker, "adopt_review_evidence",
                          side_effect=OSError("adoption denied")), \
             patch.dict(agent_worker.WORKSPACES["local-agent"],
                        {"review_evidence_root": "evidence"}):
            response, say, mocks = self.run_case("CLEAN")
        self.assertEqual(response["status"], "DONE")
        self.assertEqual(response["review_boundary"]["status"], "CLEAN")
        self.assertTrue(response["review_execution"]["adoptable"])
        self.assertEqual(response["review_evidence"]["status"], "FAILED")

    def test_nonclean_boundaries_are_done_nonadoptable_and_skip_artifacts(self):
        for boundary in ("INPUT_MODIFIED", "CANONICAL_STATE_CHANGED"):
            with self.subTest(boundary=boundary):
                response, say, mocks = self.run_case(boundary)
                self.assertEqual(response["status"], "DONE")
                self.assertFalse(response["review_execution"]["adoptable"])
                self.assertEqual(response["artifact_status"], "NOT_RUN")
                self.assertIn(boundary, response["artifact_skip_reason"])
                mocks[-1].assert_not_called()  # process_artifacts
                self.assertEqual(say.call_count, 1)

    def test_evidence_parse_exception_converges_once(self):
        response, say, mocks = self.run_case(parse_error=ValueError("bad evidence"))
        self.assertEqual(response["status"], "FAILED")
        self.assertEqual(response["failure_class"], "BRIDGE_ERROR")
        self.assertEqual(say.call_count, 1)
        mocks[7].assert_called_once()  # callback

    def test_boundary_verification_exception_converges_once(self):
        response, say, mocks = self.run_case(boundary_error=OSError("verify failed"))
        self.assertEqual(response["status"], "FAILED")
        self.assertEqual(response["failure_class"], "BRIDGE_ERROR")
        self.assertEqual(say.call_count, 1)
        mocks[7].assert_called_once()

    def test_transient_terminal_state_failure_retries_without_duplicate_slack(self):
        response, say, mocks = self.run_case(state_error=[OSError("db busy"), None])
        self.assertEqual(response["status"], "DONE")
        self.assertEqual(mocks[6].call_count, 2)
        self.assertEqual(say.call_count, 1)
        mocks[7].assert_called_once()

    def test_timeout_persists_partial_transcript_and_verifies_boundary(self):
        job = self.job(); say = Mock()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); review = self.review(root); review.root.mkdir()
            review.settings_path.write_text("{}")
            partial = json.dumps({"type":"assistant", "message":{"content":[
                {"type":"tool_use", "id":"t1", "name":"Bash",
                 "input":{"command":"git status"}}]}}) + "\n"
            timeout = ProcessTimeoutExpired(["claude"], 1, ProcessResult(-1, partial, "warning"))
            with patch.object(agent_worker, "prepare_execution",
                              return_value=({"artifact_roots": []}, root, root, {})), \
                 patch.object(agent_worker, "create_review_workspace", return_value=review), \
                 patch.object(agent_worker.claude, "run", side_effect=timeout), \
                 patch.object(agent_worker, "finish_review",
                              return_value=("CLEAN", {}, "abc", [])) as finish, \
                 patch.object(agent_worker, "diff_head_stat", return_value=""), \
                 patch.object(agent_worker, "cleanup_review", return_value="DONE"), \
                 patch.object(agent_worker.state_store, "mark_completed"), \
                 patch.object(agent_worker, "finalize_browser_callback"), \
                 patch.object(agent_worker, "dispatch_next_queued"):
                agent_worker.execute_claude_review(job, say)
            response = json.loads((root / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(response["failure_class"], "TIMEOUT")
            self.assertEqual(response["review_execution"]["actor_status"], "TIMEOUT")
            self.assertTrue(response["review_execution"]["partial_evidence_available"])
            self.assertFalse(response["review_execution"]["evidence_persisted"])
            self.assertEqual((root / "claude-stream.jsonl").read_text(encoding="utf-8"), partial)
            self.assertEqual((root / "stderr.txt").read_text(encoding="utf-8"), "warning")
            finish.assert_called_once_with(review)

    def test_log_creation_failure_converges_to_terminal_result(self):
        job = self.job(); say = Mock()
        with patch.object(agent_worker, "create_job_log", side_effect=OSError("log denied")), \
             patch.object(agent_worker.state_store, "mark_completed") as completed, \
             patch.object(agent_worker, "send_browser_callback", return_value={"status":"FAILED"}) as callback, \
             patch.object(agent_worker, "dispatch_next_queued"):
            agent_worker.execute_claude_review(job, say)
        completed.assert_called_once()
        self.assertEqual(say.call_count, 1)
        callback.assert_called_once()


if __name__ == "__main__": unittest.main()
