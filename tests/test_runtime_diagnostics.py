import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import runtime_diagnostics as diagnostics
import agent_worker


class RuntimeEvidenceTests(unittest.TestCase):
    def test_codex_config_reads_only_runtime_evidence_fields(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / ".tmp-tests") as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                'model = "gpt-test"\napi_key = "secret"\n[windows]\nsandbox = "mxc"\n',
                encoding="utf-8",
            )
            with patch.object(diagnostics, "_codex_config_path", return_value=config_path):
                self.assertEqual(diagnostics.read_codex_runtime_config(), {
                    "configured_default_model": "gpt-test",
                    "sandbox_config": "mxc",
                })

    def test_codex_effective_model_requires_execution_header(self):
        self.assertEqual(
            diagnostics.extract_codex_effective_model("", "header\nmodel: gpt-effective\n"),
            "gpt-effective",
        )
        self.assertIsNone(diagnostics.extract_codex_effective_model("done", "no header"))

    def test_claude_effective_model_requires_stream_message_field(self):
        raw = '\n'.join((
            '{"type":"system","model":"not-proof"}',
            json.dumps({"type": "assistant", "message": {"model": "claude-effective"}}),
        ))
        self.assertEqual(diagnostics.extract_claude_effective_model(raw), "claude-effective")

    def test_requested_model_is_null_without_launcher_request(self):
        result = SimpleNamespace(stdout="", stderr="model: gpt-effective\n")
        with patch.object(diagnostics, "get_cli_version", return_value="codex-cli 0.157.1"), \
             patch.object(diagnostics, "read_codex_runtime_config", return_value={
                 "configured_default_model": "gpt-default", "sandbox_config": "mxc",
             }):
            evidence = diagnostics.collect_runtime_evidence("codex", result)
        self.assertIsNone(evidence["requested_model"])
        self.assertEqual(evidence["effective_model"], "gpt-effective")


class RuntimeFailureTests(unittest.TestCase):
    def test_strong_model_error_is_classified(self):
        result = diagnostics.classify_runtime_failure(
            "The 'x' model is not supported when using Codex with a ChatGPT account."
        )
        self.assertEqual(result["classification"], "MODEL_NOT_SUPPORTED")
        self.assertIn("model/account", result["scope"])

    def test_ambiguous_error_remains_unknown(self):
        result = diagnostics.classify_runtime_failure("actor exited unexpectedly")
        self.assertEqual(result["classification"], "UNKNOWN_RUNTIME_FAILURE")

    def test_missing_executable_is_classified(self):
        result = diagnostics.classify_runtime_failure("missing", exception=FileNotFoundError())
        self.assertEqual(result["classification"], "CLI_NOT_FOUND")

    def test_diagnostic_error_redacts_actor_secrets(self):
        with patch.object(diagnostics, "build_actor_env", return_value={
            "OPENAI_API_KEY": "very-secret-value", "PATH": "bin",
        }):
            redacted = diagnostics._redact_actor_secrets(
                "request failed for very-secret-value", "codex",
            )
        self.assertEqual(redacted, "request failed for [REDACTED]")


class CheckEntryPointTests(unittest.TestCase):
    def test_check_bypasses_worker_state_and_event_loop(self):
        with patch.object(agent_worker, "run_checks", return_value=1) as run_checks, \
             patch.object(agent_worker.state_store, "initialize") as initialize, \
             patch.object(agent_worker, "SlackBridge") as bridge:
            exit_code = agent_worker.main(["--check", "codex"])
        self.assertEqual(exit_code, 1)
        run_checks.assert_called_once_with(("codex",))
        initialize.assert_not_called()
        bridge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
