import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from actors.environment import build_actor_env
from config import CLAUDE_CMD, CODEX_CMD, CODEX_SANDBOX_OVERRIDE, EXPECTED_CLI


def _codex_config_path():
    root = os.environ.get("CODEX_HOME")
    return Path(root) / "config.toml" if root else Path.home() / ".codex" / "config.toml"


def read_codex_runtime_config():
    evidence = {"configured_default_model": None, "sandbox_config": None}
    try:
        with _codex_config_path().open("rb") as handle:
            data = tomllib.load(handle)
        model = data.get("model")
        sandbox = data.get("windows", {}).get("sandbox")
        evidence["configured_default_model"] = model if isinstance(model, str) else None
        evidence["sandbox_config"] = sandbox if isinstance(sandbox, str) else None
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return evidence


def get_cli_version(command, actor):
    try:
        result = subprocess.run(
            [command, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
            env=build_actor_env(actor),
        )
        return (result.stdout.strip() or result.stderr.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def extract_codex_effective_model(stdout, stderr):
    for value in (stderr, stdout):
        match = re.search(r"(?m)^model:\s*([^\r\n]+?)\s*$", value or "")
        if match:
            return match.group(1)
    return None


def extract_claude_effective_model(raw):
    for line in (raw or "").splitlines():
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        message = item.get("message")
        if isinstance(message, dict) and isinstance(message.get("model"), str):
            return message["model"]
    return None


def collect_runtime_evidence(actor, result=None):
    command = CODEX_CMD if actor == "codex" else CLAUDE_CMD
    evidence = {
        "actor": actor,
        "cli_version": get_cli_version(command, actor),
        "configured_default_model": None,
        "requested_model": None,
        "effective_model": None,
        "sandbox_config": None,
    }
    if actor == "codex":
        evidence.update(read_codex_runtime_config())
        if result is not None:
            evidence["effective_model"] = extract_codex_effective_model(result.stdout, result.stderr)
    elif result is not None:
        evidence["effective_model"] = extract_claude_effective_model(result.stdout)
    return evidence


_FAILURES = (
    ("MODEL_NOT_SUPPORTED", ("model is not supported", "model_not_supported"), "model/account compatibility", "Select a supported default model in the shared CLI config, then smoke-test and retry."),
    ("AUTHENTICATION_FAILED", ("authentication failed", "not logged in", "unauthorized", "invalid api key"), "actor credentials", "Repair the actor login/credential configuration, then smoke-test and retry."),
    ("USAGE_LIMIT_REACHED", ("usage limit", "quota exceeded", "credit balance"), "account usage/quota", "Resolve the account usage limit, then smoke-test and retry."),
    ("RATE_LIMITED", ("rate limit", "too many requests", "status 429", '"status":429'), "provider rate limit", "Wait for the provider limit to clear, then retry."),
    ("CLI_VERSION_MISMATCH", ("version mismatch",), "local CLI installation", "Install the expected CLI version, restart the Worker, smoke-test, and retry."),
    ("SANDBOX_FAILURE", ("failed to initialize sandbox", "sandbox failure", "codex-windows-sandbox"), "local sandbox runtime", "Inspect the Windows sandbox installation/configuration; do not switch backends automatically."),
    ("PERMISSION_FAILURE", ("access is denied", "access denied", "permission denied", "os error 5"), "local filesystem/process permissions", "Repair the reported permission boundary, then smoke-test and retry."),
    ("API_TRANSPORT_FAILED", ("connection refused", "connection reset", "timed out connecting", "dns error", "network error"), "provider/network transport", "Restore network/provider reachability, then smoke-test and retry."),
)


def classify_runtime_failure(raw_error, *, exception=None):
    if isinstance(exception, FileNotFoundError):
        return {"classification": "CLI_NOT_FOUND", "scope": "local CLI installation/PATH", "repair_hint": "Install or configure the actor CLI path, restart the Worker, smoke-test, and retry."}
    text = (raw_error or "").lower()
    for classification, markers, scope, hint in _FAILURES:
        if any(marker in text for marker in markers):
            return {"classification": classification, "scope": scope, "repair_hint": hint}
    return {"classification": "UNKNOWN_RUNTIME_FAILURE", "scope": "unknown; inspect raw error", "repair_hint": "Diagnose the raw actor error before changing configuration, then smoke-test and retry."}


def _redact_actor_secrets(text, actor):
    redacted = text
    for key, value in build_actor_env(actor).items():
        if value and any(token in key for token in ("KEY", "TOKEN", "SECRET", "CREDENTIAL")):
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def _run_check(actor):
    command = CODEX_CMD if actor == "codex" else CLAUDE_CMD
    version = get_cli_version(command, actor)
    report = {
        "actor": actor, "cli_path": shutil.which(command), "cli_version": version,
        "expected_cli_version": EXPECTED_CLI[actor], "version_ok": version == EXPECTED_CLI[actor],
        "configured_default_model": None, "sandbox_config": None,
        "sandbox_invariant": None, "minimal_inference": "FAIL", "impact_scope": None,
    }
    if actor == "codex":
        report.update(read_codex_runtime_config())
        report["sandbox_invariant"] = CODEX_SANDBOX_OVERRIDE
    if report["cli_path"] is None:
        report["impact_scope"] = "CLI_NOT_FOUND: this actor cannot run."
        return report
    if not report["version_ok"]:
        report["impact_scope"] = "CLI_VERSION_MISMATCH: this actor is outside the tested version gate."
        return report
    try:
        with tempfile.TemporaryDirectory(prefix=f"local-agent-check-{actor}-") as temp_dir:
            if actor == "codex":
                args = [command, "exec", "--skip-git-repo-check", "--ephemeral", "--json", "-c", CODEX_SANDBOX_OVERRIDE, "--sandbox", "read-only", "-"]
            else:
                settings = Path(temp_dir) / "settings.json"
                settings.write_text("{}", encoding="utf-8")
                args = [command, "-p", "--restricted", "--tools", "", "--settings", str(settings), "--safe-mode", "--strict-mcp-config", "--permission-mode", "dontAsk", "--permission-prompts", "none", "--no-session-persistence", "--output-format", "stream-json", "--verbose"]
            result = subprocess.run(args, cwd=temp_dir, input="Reply exactly LOCAL_AGENT_CHECK_OK. Do not use tools.", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, env=build_actor_env(actor))
        passed = result.returncode == 0 and "LOCAL_AGENT_CHECK_OK" in result.stdout
        report["minimal_inference"] = "PASS" if passed else "FAIL"
        report["effective_model"] = extract_codex_effective_model(result.stdout, result.stderr) if actor == "codex" else extract_claude_effective_model(result.stdout)
        if passed:
            report["impact_scope"] = "No actor runtime failure detected."
        else:
            raw = _redact_actor_secrets(result.stderr.strip() or result.stdout.strip(), actor)
            diagnostic = classify_runtime_failure(raw)
            report["impact_scope"] = f"{diagnostic['classification']}: {diagnostic['scope']}"
            report["error"] = raw[-1000:]
    except (OSError, subprocess.SubprocessError) as exc:
        diagnostic = classify_runtime_failure(str(exc), exception=exc)
        report["impact_scope"] = f"{diagnostic['classification']}: {diagnostic['scope']}"
        report["error"] = _redact_actor_secrets(str(exc), actor)[:1000]
    return report


def run_checks(actors):
    reports = [_run_check(actor) for actor in actors]
    print(json.dumps({"checks": reports}, ensure_ascii=False, indent=2))
    return 0 if all(r["minimal_inference"] == "PASS" for r in reports) else 1
