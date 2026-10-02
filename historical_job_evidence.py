"""Manual, Worker-owned adoption of bounded historical Codex job evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from review_evidence import (
    _assert_safe_existing_parents,
    _is_reparse,
    _package_matches,
    _safe_relative,
    _tree_snapshot,
)


SCHEMA = "worker-job-evidence"
VERSION = 1
EXTRACTION_METHOD = "codex-exec-plain-stdout-terminal-message-v1"
TRUST_LIMITATION = (
    "Bytes were hashed at adoption time; an original terminal-time cryptographic "
    "anchor does not exist. Corroboration does not make ACTOR_REPORTED content "
    "Worker-observed truth. This package is not a standalone safety gate."
)
RAW_NAMES = ("stdout.txt", "stderr.txt")


class HistoricalEvidenceError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def extract_final_actor_message(raw_stdout: bytes) -> str:
    """Extract Codex's terminal message from plain ``codex exec`` stdout.

    In the inspected historical and current format, stdout is a dedicated final
    message channel and progress/tool events are emitted on stderr.  V1 therefore
    accepts the complete stdout iff it is one non-empty, strict UTF-8 text stream.
    Only CRLF/bare-CR newline spelling is normalized.  NUL and ANSI control
    sequences fail closed because they indicate a mixed/non-text stream.
    """
    try:
        text = raw_stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HistoricalEvidenceError("stdout is not strict UTF-8") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise HistoricalEvidenceError("terminal actor message is missing")
    if "\x00" in text or "\x1b[" in text:
        raise HistoricalEvidenceError("stdout is not an unambiguous plain message stream")
    return text


def _load_object(path: Path, label: str) -> dict:
    if not path.is_file() or path.is_symlink():
        raise HistoricalEvidenceError(f"missing or unsafe {label}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalEvidenceError(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise HistoricalEvidenceError(f"invalid {label}")
    return value


def _same(label: str, values: list[tuple[str, object]]) -> list[str]:
    present = [(source, value) for source, value in values if value is not None]
    if len(present) > 1 and any(value != present[0][1] for _, value in present[1:]):
        raise HistoricalEvidenceError(
            f"provenance mismatch for {label}: " + ", ".join(source for source, _ in present)
        )
    return [source for source, _ in present]


def _instruction_ref(value):
    if value is None or isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HistoricalEvidenceError("invalid state_store instruction_ref") from exc
    if not isinstance(parsed, dict):
        raise HistoricalEvidenceError("invalid state_store instruction_ref")
    return parsed


def _corroborate(request: dict, result: dict, state: dict, slack: dict) -> dict:
    req_sha = request.get("instruction_sha256", request.get("prompt_sha256"))
    res_sha = result.get("instruction_sha256", result.get("prompt_sha256"))
    slack_git = slack.get("git") if isinstance(slack.get("git"), dict) else {}
    result_git = result.get("git") if isinstance(result.get("git"), dict) else {}
    comparisons = {
        "protocol_version": [("request", request.get("protocol_version")),
                             ("result", result.get("protocol_version")),
                             ("state_store", state.get("protocol_version")),
                             ("slack_result_manifest", slack.get("protocol_version"))],
        "job_id": [("request", request.get("job_id")), ("result", result.get("job_id")),
                   ("state_store", state.get("job_id")), ("slack_result_manifest", slack.get("job_id"))],
        "actor": [("request", request.get("actor")), ("result", result.get("actor")),
                  ("state_store", state.get("actor")), ("slack_result_manifest", slack.get("actor"))],
        "mode": [("request", request.get("mode")), ("result", result.get("mode")),
                 ("state_store", state.get("mode")), ("slack_result_manifest", slack.get("mode"))],
        "workspace": [("request", request.get("workspace")), ("result", result.get("workspace")),
                      ("state_store", state.get("workspace")), ("slack_result_manifest", slack.get("workspace"))],
        "instruction_sha256": [("request", req_sha), ("result", res_sha),
                               ("state_store", state.get("prompt_sha256")),
                               ("slack_result_manifest", slack.get("instruction_sha256"))],
        "instruction_ref": [("request", request.get("instruction_ref")),
                            ("result", result.get("instruction_ref")),
                            ("state_store", _instruction_ref(state.get("instruction_ref"))),
                            ("slack_result_manifest", slack.get("instruction_ref"))],
        "status": [("result", result.get("status")), ("state_store", state.get("status")),
                   ("slack_result_manifest", slack.get("status"))],
        "exit_code": [("result", result.get("exit_code")), ("state_store", state.get("exit_code")),
                      ("slack_result_manifest", slack.get("exit_code"))],
        "failure_class": [("result", result.get("failure_class")),
                          ("state_store", state.get("failure_class")),
                          ("slack_result_manifest", slack.get("failure_class"))],
        "git.baseline_commit": [("result", result_git.get("baseline_commit")),
                                ("slack_result_manifest", slack_git.get("baseline_commit"))],
        "git.head_after": [("result", result_git.get("head_after")),
                           ("slack_result_manifest", slack_git.get("head_after"))],
        "git.changed_paths": [("result", result_git.get("changed_paths")),
                              ("slack_result_manifest", slack_git.get("changed_paths"))],
        "runtime": [("result", result.get("runtime")),
                    ("slack_result_manifest", slack.get("runtime"))],
        "artifact_status": [("result", result.get("artifact_status")),
                            ("slack_result_manifest", slack.get("artifact_status"))],
        "artifacts": [("result", result.get("artifacts")),
                      ("slack_result_manifest", slack.get("artifacts"))],
        "rejected_artifacts": [("result", result.get("rejected_artifacts")),
                               ("slack_result_manifest", slack.get("rejected_artifacts"))],
        "summary": [("result", result.get("summary")),
                    ("slack_result_manifest", slack.get("summary"))],
    }
    required_slack = ("job_id", "actor", "mode", "workspace", "instruction_sha256",
                      "status", "exit_code", "git")
    if any(key not in slack for key in required_slack):
        raise HistoricalEvidenceError("Slack Result Manifest lacks required stable fields")
    if any(key not in slack_git for key in ("baseline_commit", "head_after")):
        raise HistoricalEvidenceError("Slack Result Manifest lacks required git fields")
    matched = {field: _same(field, values) for field, values in comparisons.items()
               if any(value is not None for _, value in values)}
    return {
        "authority": "CORROBORATIVE_ONLY",
        "state_store": {"status": "MATCHED", "matched_fields": sorted(
            field for field, sources in matched.items() if "state_store" in sources)},
        "slack_result_manifest": {"status": "MATCHED", "sha256": None,
            "matched_fields": sorted(field for field, sources in matched.items()
                                     if "slack_result_manifest" in sources)},
    }


def _base(status: str, *, job_id, workspace, destination=None, error=None) -> dict:
    value = {"status": status, "mode": "HISTORICAL_MANUAL", "job_id": job_id,
             "workspace": workspace, "destination": destination,
             "manifest_sha256": None, "corroboration": None,
             "trust_limitation": TRUST_LIMITATION}
    if error is not None:
        value["error"] = str(error)[:4000]
    return value


def adopt_historical_job_evidence(*, canonical, evidence_root, job_id, workspace,
                                  log_dir, state_row, slack_manifest_path,
                                  human_approved: bool) -> dict:
    """Build and atomically install a HISTORICAL_MANUAL evidence package."""
    destination_rel = None
    staging = None
    installed = False
    corroboration = None
    try:
        if not human_approved:
            raise HistoricalEvidenceError("explicit --human-approved is required")
        if not state_row or state_row.get("job_id") != job_id:
            raise HistoricalEvidenceError("exact job_id is absent from state_store")
        canonical = Path(os.path.abspath(canonical))
        if not canonical.is_dir():
            raise HistoricalEvidenceError("canonical workspace does not exist")
        root_rel = _safe_relative(evidence_root, label="job_evidence_root")
        job_rel = _safe_relative(job_id, label="job_id")
        root = canonical / root_rel
        destination = root / job_rel
        destination_rel = destination.relative_to(canonical).as_posix()
        if root == canonical or canonical not in root.parents or root not in destination.parents:
            raise HistoricalEvidenceError("evidence destination escaped canonical workspace")
        _assert_safe_existing_parents(canonical, destination)

        log_dir = Path(log_dir)
        request = _load_object(log_dir / "request.json", "request.json")
        result = _load_object(log_dir / "result.json", "result.json")
        slack_path = Path(slack_manifest_path)
        slack = _load_object(slack_path, "Slack Result Manifest")
        corroboration = _corroborate(request, result, state_row, slack)
        slack_bytes = slack_path.read_bytes()
        corroboration["slack_result_manifest"]["sha256"] = _sha256(slack_bytes)

        stdout_path = log_dir / "stdout.txt"
        if not stdout_path.is_file() or stdout_path.is_symlink():
            raise HistoricalEvidenceError("missing or unsafe stdout.txt")
        raw_stdout = stdout_path.read_bytes()
        actor_message = extract_final_actor_message(raw_stdout)
        message_bytes = actor_message.encode("utf-8")
        message_hash = _sha256(message_bytes)

        worker_fields = ("protocol_version", "job_id", "actor", "mode", "workspace",
                         "prompt_sha256", "instruction_ref", "instruction_sha256", "status",
                         "runtime", "runtime_diagnostics", "exit_code", "failure_class",
                         "error_summary", "git", "artifact_status", "artifacts",
                         "rejected_artifacts")
        worker_observed = {key: result[key] for key in worker_fields if key in result}
        actor_reported = {"authority": "ACTOR_REPORTED", "source_job_id": job_id,
                          "summary": result.get("summary"),
                          "final_actor_message": actor_message,
                          "final_actor_message_sha256": message_hash,
                          "extraction": {"method": EXTRACTION_METHOD, "version": 1,
                                         "newline_normalization": "CRLF and CR to LF"}}
        normalized = {"schema": "normalized-historical-job-result", "version": 1,
                      "worker_observed": worker_observed,
                      "state_store_observed": {key: state_row.get(key) for key in (
                          "received_at", "queued_at", "started_at", "heartbeat_at",
                          "completed_at", "host") if state_row.get(key) is not None},
                      "actor_reported": {"summary": result.get("summary")}}
        package = {"normalized-result.json": _canonical_json(normalized),
                   "actor-reported.json": _canonical_json(actor_reported)}
        adopted = [{"path": name, "sha256": _sha256(data)}
                   for name, data in sorted(package.items())]
        raw = []
        for name in RAW_NAMES:
            source = log_dir / name
            if source.is_file() and not source.is_symlink():
                raw.append({"path": name, "sha256": _sha256(source.read_bytes()),
                            "storage": "LOCAL_ONLY"})
        manifest = {
            "schema": SCHEMA, "version": VERSION,
            "adoption": {"mode": "HISTORICAL_MANUAL", "human_approved": True,
                         "owner": "Local Agent Worker"},
            "job_id": job_id, "actor": request.get("actor"), "mode": request.get("mode"),
            "workspace": workspace, "instruction_ref": request.get("instruction_ref"),
            "instruction_sha256": request.get("instruction_sha256", request.get("prompt_sha256")),
            "authority": {
                "worker_observed": "Persisted Worker-recorded facts in normalized-result.json",
                "actor_reported": "Codex report/judgment; not independently proven",
                "raw_local_only": "Raw execution logs are hashed but not copied",
                "corroborative_only": "Cross-checks strengthen provenance only",
            },
            "worker_observed": {"file": "normalized-result.json",
                                "sha256": _sha256(package["normalized-result.json"])},
            "actor_reported": {"file": "actor-reported.json",
                               "sha256": _sha256(package["actor-reported.json"]),
                               "message_sha256": message_hash,
                               "extraction_method": EXTRACTION_METHOD},
            "raw_local_only": raw,
            "source_provenance": {"log_location": str(log_dir), "storage": "LOCAL_ONLY"},
            "corroboration": corroboration,
            "trust_limitation": TRUST_LIMITATION,
            "adopted_files": adopted,
            "serialization": "UTF-8 canonical JSON; sorted keys; compact separators; LF suffix",
        }
        package["job-evidence-manifest.json"] = _canonical_json(manifest)
        manifest_hash = _sha256(package["job-evidence-manifest.json"])

        before = _tree_snapshot(canonical, destination)
        root.mkdir(parents=True, exist_ok=True)
        _assert_safe_existing_parents(canonical, destination)
        if destination.exists():
            if (not destination.is_dir() or _is_reparse(destination)
                    or not _package_matches(destination, package)):
                raise HistoricalEvidenceError(
                    "evidence destination already contains a different package")
            status = "NOOP"
        else:
            staging = Path(tempfile.mkdtemp(prefix=".job-evidence-", dir=root))
            for name, data in package.items():
                (staging / name).write_bytes(data)
            os.replace(staging, destination)
            staging = None
            installed = True
            status = "ADOPTED"
        after = _tree_snapshot(canonical, destination)
        if before != after:
            if installed:
                shutil.rmtree(destination, ignore_errors=True)
            raise HistoricalEvidenceError(
                "canonical content changed outside the job evidence destination")
        value = _base(status, job_id=job_id, workspace=workspace,
                      destination=destination_rel)
        value.update({"manifest_sha256": manifest_hash, "corroboration": corroboration})
        return value
    except Exception as exc:
        value = _base("FAILED", job_id=job_id, workspace=workspace,
                      destination=destination_rel, error=exc)
        value["corroboration"] = corroboration
        return value
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
