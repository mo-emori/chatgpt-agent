import json
import hashlib
import logging
import subprocess
from dataclasses import replace
import threading
import argparse
import sys
import os
from pathlib import Path

from actors import run_agent
from actors import claude
from actors.claude_stream import parse_stream_json
from review_invocation import (PackageLaunchError,
    prepare as prepare_review_invocation,
    prepare_effective_prompt, telemetry as review_telemetry)
from actors.process_runner import ProcessResult
from actors.review_workspace import (
    ReviewPreparationError, cleanup_review, create_review_workspace, diff_head_stat, finish_review,
)
from config import (HOSTNAME, CONTEXT_HARNESS_ACTIVATION_MODE,
                    CONTEXT_HARNESS_ENFORCE_CAPABILITIES)
from job_protocol import (
    Job,
    JobValidationError,
    job_from_row,
    parse_job,
)
from slack_bridge import SlackBridge
import state_store
from artifacts.manifest import (
    ManifestError,
    parse_agent_result,
)
from artifacts.validator import (
    ArtifactPathError,
    validate_artifact_path,
)
from config import WORKSPACES
from drive_store import upload_artifacts
from cli_check import check_cli_versions
from runtime_diagnostics import (
    classify_runtime_failure,
    collect_runtime_evidence,
    run_checks,
)
from review_evidence import (
    adopt_review_evidence, failed_review_evidence, not_run_review_evidence,
)
from review_decision import parse_block
from historical_job_evidence import adopt_historical_job_evidence
from historical_review_decision import adopt_historical_review_decision
from job_log import (
    create_job_log,
    get_attributable_changed_paths,
    get_changed_paths,
    get_git_snapshot,
    save_git_snapshot,
    save_json,
    save_text,
)
from browser.notify import (
    BrowserNotifyError,
    SUBMITTED_ACK_UNVERIFIED,
    notify_chatgpt,
)
from dataclasses import replace
from notion_client import (
    NotionInstructionError,
    fetch_instruction,
)
from operational_logging import configure_logging, emit_lifecycle
from single_instance import SingleInstanceAlreadyRunning, WorkerInstanceGuard
from context_harness import begin_shadow, finish_shadow, refresh_evidence_index
from context_activation import (has_context_request, prepare as prepare_context_activation,
                                resolve_activation_mode)
import context_trust
from job_protocol import TRUST_CONTROL_OPERATION

logger = logging.getLogger(__name__)
_execution_context = threading.local()


def log_job_start(job, baseline=None):
    if getattr(_execution_context, "lifecycle_started", None) == job.job_id:
        return
    _execution_context.lifecycle_started = job.job_id
    baseline_line = f"\nbaseline : {baseline}" if baseline is not None else ""
    emit_lifecycle(
        "\n==============================\n"
        f"JOB START\njob_id   : {job.job_id}\nactor    : {job.actor}\n"
        f"mode     : {job.mode}\nworkspace: {job.workspace}\nstatus   : RUNNING"
        f"{baseline_line}\n=============================="
    )

def send_json(say, data):
    say(
        "```json\n"
        + json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
        + "\n```"
    )


def send_browser_callback(
    job,
    *,
    status,
    artifact_status,
    failure_class=None,
):
    if (
        job.callback_type is None
        or job.callback_url is None
    ):
        return {
            "type": None,
            "status": "NOT_REQUESTED",
        }

    if (
        job.callback_type
        != "chatgpt_browser"
    ):
        logger.warning(
            "Browser callback failed: job_id=%s unknown callback type=%s",
            job.job_id,
            job.callback_type,
        )
        return {
            "type":
                job.callback_type,
            "status": "FAILED",
            "error":
                "UNKNOWN_CALLBACK_TYPE",
        }

    terminal_marker = (
        "LOCAL_AGENT_JOB_COMPLETED"
        if status == "DONE"
        else "LOCAL_AGENT_JOB_FAILED"
    )
    failure_line = (
        f"failure_class: {failure_class}\n"
        if failure_class is not None
        else ""
    )
    closure = (
        "Inspect the Slack Result Manifest and continue Job Closure."
        if status == "DONE"
        else "Inspect the Slack Result Manifest and continue Failure Closure."
    )
    message = (
        f"{terminal_marker}\n\n"
        f"job_id: {job.job_id}\n"
        f"actor: {job.actor}\n"
        f"workspace: {job.workspace}\n"
        f"status: {status}\n"
        f"{failure_line}"
        f"artifact_status: {artifact_status}\n\n"
        f"{closure}"
    )

    try:
        delivery_status = notify_chatgpt(
            target_url=
                job.callback_url,
            message=message,
        )
        if delivery_status == SUBMITTED_ACK_UNVERIFIED:
            logger.warning(
                "Browser callback submitted; identity ACK unavailable: job_id=%s",
                job.job_id,
            )
            return {
                "type": "chatgpt_browser",
                "status": SUBMITTED_ACK_UNVERIFIED,
                "url": job.callback_url,
            }
        logger.info("Browser callback succeeded: job_id=%s", job.job_id)

        return {
            "type":
                "chatgpt_browser",
            "status":
                "DONE",
            "url": job.callback_url,
        }

    except BrowserNotifyError as e:
        logger.warning("BROWSER CALLBACK FAILED: %s", e)

        return {
            "type":
                "chatgpt_browser",
            "status":
                "FAILED",
            "error":
                str(e),
            "url": job.callback_url,
        }


def resolve_v3_instruction(job):
    instruction = fetch_instruction(
        job.instruction_ref["page_id"]
    )

    return replace(
        job,
        prompt=instruction["text"],
        prompt_sha256=instruction[
            "sha256"
        ],
    )


def reject_pre_dispatch(say, job, *, failure_class, error_summary):
    """Publish the authoritative Slack rejection before an optional wake-up."""
    response = {
        "status": "BRIDGE_ERROR",
        "failure_class": failure_class,
        "error_summary": error_summary,
    }
    if job is not None:
        response.update({"protocol_version": job.protocol_version, "job_id": job.job_id})
    send_json(say, response)
    if (
        job is not None
        and job.callback_type is not None
        and job.callback_url is not None
    ):
        send_browser_callback(
            job,
            status="BRIDGE_ERROR",
            artifact_status="NOT_RUN",
            failure_class=failure_class,
        )


def process_message(
    *,
    text,
    channel,
    sender,
    say,
):
    # JOB以外の普通のSlack会話は無視
    if '"job_id"' not in text:
        return

    try:
        job = parse_job(text)

    except JobValidationError as e:
        logger.error("JOB validation failed: %s", e)
        reject_pre_dispatch(
            say,
            getattr(e, "callback_job", None),
            failure_class="INVALID_JOB",
            error_summary=str(e),
        )
        return

    # Idempotency
    existing = state_store.get_job(
        job.job_id
    )

    if existing is not None:
        logger.info("Duplicate JOB ignored: job_id=%s status=%s", job.job_id, existing["status"])

        send_json(
            say,
            {
                "protocol_version":
                    job.protocol_version,
                "job_id": job.job_id,
                "status": "DUPLICATE_JOB",
                "existing_status":
                    existing["status"],
            },
        )
        return

    if (job.protocol_version == "3" and
            getattr(job, "operation", None) != TRUST_CONTROL_OPERATION):
        try:
            job = resolve_v3_instruction(
                job
            )

        except NotionInstructionError as e:
            failure_class = str(e)

            logger.error("INSTRUCTION RESOLVE FAILED: job_id=%s failure=%s", job.job_id, failure_class)

            reject_pre_dispatch(
                say,
                job,
                failure_class=failure_class,
                error_summary=failure_class,
            )

            return

    # RECEIVED
    state_store.create_job(job)

    # JOB validation completed
    state_store.set_status(
        job.job_id,
        "VALIDATED",
    )

    state_store.mark_queued(
        job.job_id
    )

    dispatch_next_queued(
        job.workspace,
        say,
    )

    if state_store.get_job(job.job_id)["status"] == "QUEUED":

        send_json(
            say,
            {
                "protocol_version":
                    job.protocol_version,
                "job_id": job.job_id,
                "status": "QUEUED",
                "workspace": job.workspace,
            },
        )

        logger.info("JOB QUEUED: job_id=%s", job.job_id)

    return


def dispatch_next_queued(workspace, say):
    while True:
        row = state_store.claim_next_queued(workspace)

        if row is None:
            return

        try:
            job = job_from_row(row)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as e:
            state_store.mark_completed(
                row["job_id"],
                status="FAILED",
                failure_class="INCOMPLETE_QUEUED_STATE",
            )
            logger.warning("UNRECOVERABLE QUEUED JOB: job_id=%s error=%s", row["job_id"], e)
            send_json(
                say,
                {
                    "protocol_version": row["protocol_version"],
                    "job_id": row["job_id"],
                    "status": "FAILED",
                    "failure_class": "INCOMPLETE_QUEUED_STATE",
                    "error_summary": str(e),
                },
            )
            continue

        try:
            validate_recoverable_queued_job(job)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as e:
            state_store.mark_completed(
                row["job_id"],
                status="FAILED",
                failure_class="INCOMPLETE_QUEUED_STATE",
            )
            logger.warning("UNRECOVERABLE QUEUED JOB: job_id=%s error=%s", row["job_id"], e)
            send_json(
                say,
                {
                    "protocol_version": row["protocol_version"],
                    "job_id": row["job_id"],
                    "status": "FAILED",
                    "failure_class": "INCOMPLETE_QUEUED_STATE",
                    "error_summary": str(e),
                },
            )
            send_browser_callback(
                job,
                status="FAILED",
                artifact_status="NOT_RUN",
                failure_class="INCOMPLETE_QUEUED_STATE",
            )
            continue

        break

    logger.info("DISPATCH QUEUED JOB: job_id=%s", job.job_id)

    thread = threading.Thread(
        target=execute_claimed_job,
        args=(job, say),
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        if not state_store.restore_claim(job.job_id):
            state_store.mark_completed(
                job.job_id,
                status="FAILED",
                failure_class="DISPATCH_START_FAILED",
            )
            response = build_result(
                job,
                status="FAILED",
                failure_class="DISPATCH_START_FAILED",
            )
            send_json(say, response)
            send_browser_callback(
                job,
                status="FAILED",
                artifact_status="NOT_RUN",
                failure_class="DISPATCH_START_FAILED",
            )
        raise


def execute_claimed_job(job, say):
    """Run a claimed job and fail closed if setup fails before execution."""
    try:
        _execution_context.claimed = True
        _execution_context.lifecycle_started = None
        _execution_context.lifecycle_ended = None
        state_store.mark_running(job.job_id, os.getpid(), HOSTNAME)
        log_job_start(job)
        execute_job(job, say)
    except Exception as e:
        state_store.mark_completed(
            job.job_id,
            status="FAILED",
            failure_class="DISPATCH_START_FAILED",
        )
        response = build_result(
            job,
            status="FAILED",
            failure_class="DISPATCH_START_FAILED",
            error_summary=str(e)[:4000],
        )
        send_json(say, response)
        try:
            send_browser_callback(
                job,
                status="FAILED",
                artifact_status="NOT_RUN",
                failure_class="DISPATCH_START_FAILED",
            )
        except Exception:
            logger.exception("Failure callback failed: job_id=%s", job.job_id)
        logger.exception("DISPATCH START FAILED: job_id=%s", job.job_id)
    finally:
        row = state_store.get_job(job.job_id) or {}
        log_job_end(job, row.get("status", "FAILED"), row.get("exit_code"))
        dispatch_next_queued(job.workspace, say)
        _execution_context.claimed = False


def validate_recoverable_queued_job(job):
    if job.protocol_version != "3":
        return

    if getattr(job, "operation", None) == TRUST_CONTROL_OPERATION:
        if job.actor is not None or job.mode is not None or job.instruction_ref is not None:
            raise ValueError("persisted control operation contains actor fields")
        if job.control_action not in context_trust_control_actions():
            raise ValueError("persisted control action is invalid")
        return

    if not isinstance(job.prompt, str) or not job.prompt:
        raise ValueError("persisted v3 prompt snapshot is missing")

    if (
        not isinstance(job.prompt_sha256, str)
        or len(job.prompt_sha256) != 64
        or any(c not in "0123456789abcdef" for c in job.prompt_sha256)
    ):
        raise ValueError("persisted v3 prompt SHA-256 is missing or invalid")

    actual_sha256 = hashlib.sha256(
        job.prompt.encode("utf-8")
    ).hexdigest()
    if actual_sha256 != job.prompt_sha256:
        raise ValueError("persisted v3 prompt snapshot SHA-256 mismatch")

    ref = job.instruction_ref
    if (
        not isinstance(ref, dict)
        or ref.get("type") != "notion_page"
        or not isinstance(ref.get("page_id"), str)
        or not ref["page_id"].strip()
    ):
        raise ValueError("persisted v3 instruction_ref is missing or invalid")


def context_trust_control_actions():
    return {"TRUST_INSPECT", "TRUST_ACCEPT", "TRUST_LEGACY_AUTO_MIGRATE"}


def trust_failure_code(message):
    value = message.lower()
    for needle, code in (
        ("candidate hash", "CANDIDATE_HASH_MISMATCH"),
        ("current trusted hash", "TRUSTED_BASELINE_CAS_MISMATCH"),
        ("legacy trusted hash", "LEGACY_BASELINE_CAS_MISMATCH"),
        ("trust domain", "TRUST_DOMAIN_MISMATCH"),
        ("stale", "CANDIDATE_STALE"),
        ("new-format baseline", "LEGACY_MIGRATION_NOT_ELIGIBLE"),
        ("not a legacy", "LEGACY_MIGRATION_NOT_ELIGIBLE"),
        ("reconciliation evidence", "RECONCILIATION_EVIDENCE_INCOMPLETE"),
        ("receipt", "RECEIPT_VALIDATION_FAILED"),
    ):
        if needle in value:
            return code
    return "TRUST_VALIDATION_FAILED"


def execute_trust_control(job, say):
    """Execute the closed trust control plane before any actor/context activation."""
    log_dir = create_job_log(job.job_id)
    request = job.trust_request or {}
    workspace = WORKSPACES[job.workspace]
    common = {
        "root": workspace["path"],
        "cache_root": Path(__file__).parent / "logs",
        "workspace": job.workspace,
        "capability": request["capability"],
    }
    save_json(log_dir, "request.json", {
        "protocol_version": job.protocol_version, "job_id": job.job_id,
        "workspace": job.workspace, "operation": job.operation,
        "control_action": job.control_action, "trust": request,
        "callback": {"type": job.callback_type, "url": job.callback_url},
    })
    status = "FAILED"
    failure_class = None
    try:
        if job.control_action == "TRUST_INSPECT":
            outcome = context_trust.inspect_trust(**common)
        elif job.control_action == "TRUST_ACCEPT":
            outcome = context_trust.accept_candidate(
                **common,
                expected_candidate_sha256=request["expected_candidate_sha256"],
                expected_current_trusted_sha256=request.get(
                    "expected_current_trusted_sha256"),
                operator=request["operator"], reason=request["reason"])
        elif job.control_action == "TRUST_LEGACY_AUTO_MIGRATE":
            outcome = context_trust.legacy_auto_migrate(
                **common,
                expected_candidate_sha256=request["expected_candidate_sha256"],
                expected_legacy_trusted_sha256=request[
                    "expected_legacy_trusted_sha256"],
                operator=request["operator"], reason=request["reason"])
        else:
            raise context_trust.AcceptanceError("unknown control action")
        status = "DONE"
        response = {
            "protocol_version": job.protocol_version, "job_id": job.job_id,
            "workspace": job.workspace, "operation": job.operation,
            "control_action": job.control_action, "status": status,
            "actor_started": False, "actor": None, "effective_model": None,
            "trust_domain": {"workspace": job.workspace,
                             "capability": request["capability"]},
            **outcome,
        }
        if request.get("expected_candidate_sha256") is not None:
            response.setdefault("candidate_manifest_sha256",
                                request["expected_candidate_sha256"])
        if outcome.get("previous_trusted_manifest_sha256") is not None:
            response.setdefault("old_trusted_manifest_sha256",
                                outcome["previous_trusted_manifest_sha256"])
        if outcome.get("trusted_manifest_sha256") is not None:
            response.setdefault("new_trusted_manifest_sha256",
                                outcome["trusted_manifest_sha256"])
    except context_trust.AcceptanceError as exc:
        failure_class = "TRUST_CONTROL_REJECTED"
        response = {
            "protocol_version": job.protocol_version, "job_id": job.job_id,
            "workspace": job.workspace, "operation": job.operation,
            "control_action": job.control_action, "status": "REJECTED",
            "failure_class": failure_class,
            "validation_reason_codes": [trust_failure_code(str(exc))],
            "error_summary": str(exc), "actor_started": False, "actor": None,
            "effective_model": None,
            "trust_domain": {"workspace": job.workspace,
                             "capability": request.get("capability")},
        }
    except Exception as exc:
        failure_class = "TRUST_CONTROL_ERROR"
        response = {
            "protocol_version": job.protocol_version, "job_id": job.job_id,
            "workspace": job.workspace, "operation": job.operation,
            "control_action": job.control_action, "status": "FAILED",
            "failure_class": failure_class,
            "validation_reason_codes": ["TRUST_CONTROL_INTERNAL_ERROR"],
            "error_summary": str(exc)[:4000], "actor_started": False,
            "actor": None, "effective_model": None,
            "trust_domain": {"workspace": job.workspace,
                             "capability": request.get("capability")},
        }
    state_store.mark_completed(job.job_id, status=status,
                               failure_class=failure_class)
    publish_slack_result(log_dir, say, response)
    finalize_browser_callback(job, log_dir, response, status=response["status"],
                              artifact_status="NOT_APPLICABLE",
                              failure_class=failure_class)


def recover_queued_jobs(say):
    state_store.restore_dispatching_jobs()
    workspaces = state_store.list_queued_workspaces()

    if not workspaces:
        return

    logger.warning("Queue recovery: %d workspace(s)", len(workspaces))

    for workspace in workspaces:
        dispatch_next_queued(workspace, say)


def prepare_execution(job, *, log_dir=None):
    workspace = WORKSPACES[job.workspace]
    workdir = workspace["path"]
    log_dir = log_dir or create_job_log(job.job_id)

    request_data = {
        "protocol_version": job.protocol_version,
        "job_id": job.job_id,
        "actor": job.actor,
        "mode": job.mode,
        "workspace": job.workspace,
        "prompt_sha256": job.prompt_sha256,
        "callback": {
            "type": job.callback_type,
            "url": job.callback_url,
        },
    }

    if job.protocol_version == "3":
        request_data["instruction_ref"] = (
            job.instruction_ref
        )
        request_data["instruction_sha256"] = (
            job.prompt_sha256
        )
        if job.review_mode is not None:
            request_data["review_mode"] = job.review_mode
        if job.review_package_ref is not None:
            request_data["review_package_ref"] = job.review_package_ref
        if job.measurement_mode:
            request_data["measurement_mode"] = True

    save_json(
        log_dir,
        "request.json",
        request_data,
    )

    before = get_git_snapshot(workdir)
    save_git_snapshot(log_dir, "before", before)

    log_job_start(job, before["head"])

    return workspace, workdir, log_dir, before


def collect_execution_evidence(
    result,
    *,
    workdir,
    log_dir,
    before,
):
    save_text(log_dir, "stdout.txt", result.stdout)
    save_text(log_dir, "stderr.txt", result.stderr)

    after = get_git_snapshot(workdir)
    save_git_snapshot(log_dir, "after", after)

    return after, get_changed_paths(before, after)


def process_artifacts(
    job,
    result,
    workspace,
    workdir,
    canonical_changed_paths=(),
):
    artifact_status = "DONE"
    artifacts = []
    rejected_artifacts = []
    canonical_references = []
    agent_summary = result.stdout.strip()
    changed_paths = {
        Path(path).as_posix()
        for path in canonical_changed_paths
    }

    try:
        agent_result = parse_agent_result(result.stdout)
        agent_summary = agent_result.summary
        validated = []

        for raw_path in agent_result.artifacts:
            try:
                path = validate_artifact_path(
                    raw_path,
                    workspace_root=workdir,
                    artifact_roots=workspace["artifact_roots"],
                )
                validated.append((raw_path, path))
            except ArtifactPathError as e:
                if str(e) == "Artifact outside allowed roots":
                    try:
                        canonical_path = validate_artifact_path(
                            raw_path,
                            workspace_root=workdir,
                            artifact_roots=[],
                        )
                        relative_path = canonical_path.relative_to(
                            Path(workdir).resolve()
                        ).as_posix()
                        if relative_path in changed_paths:
                            canonical_references.append({
                                "path": relative_path,
                                "disposition": "REPO_CANONICAL_REFERENCE",
                                "reason": (
                                    "Outside external artifact roots and "
                                    "attributed to the Worker-observed "
                                    "before/after canonical change set"
                                ),
                            })
                            continue
                    except (ArtifactPathError, ValueError):
                        pass
                logger.warning(
                    "Rejected artifact: job_id=%s path=%s reason=%s",
                    job.job_id, raw_path, e,
                )
                rejected_artifacts.append({
                    "path": raw_path,
                    "reason": str(e),
                })

        # Validate every path before uploading the allowed artifacts.
        if validated:
            uploaded = upload_artifacts(
                job.job_id,
                [path for _, path in validated],
            )
            for (source_path, _), drive_item in zip(
                validated,
                uploaded,
                strict=True,
            ):
                artifacts.append({
                    "name": drive_item["name"],
                    "source_path": source_path,
                    "drive_file_id": drive_item["drive_file_id"],
                })

        if rejected_artifacts:
            artifact_status = (
                "PARTIAL_FAILURE" if artifacts else "FAILED"
            )
    except ManifestError as e:
        logger.warning("Rejected artifact manifest: job_id=%s reason=%s", job.job_id, e)
        artifact_status = "FAILED"
        rejected_artifacts.append({
            "path": None,
            "reason": f"Manifest error: {e}",
        })
    except Exception as e:
        logger.exception("Artifact processing failed: job_id=%s", job.job_id)
        # Artifact transport failure must not change execution status.
        artifact_status = "FAILED"
        rejected_artifacts.append({
            "path": None,
            "reason": f"Artifact processing error: {e}",
        })

    return (
        agent_summary,
        artifact_status,
        artifacts,
        rejected_artifacts,
        canonical_references,
    )


def unpack_artifact_result(artifact_result):
    """Accept legacy four-field internal results while adding diagnostics."""
    if len(artifact_result) == 4:
        return (*artifact_result, [])
    return artifact_result


def build_result(
    job,
    *,
    status,
    failure_class=None,
    exit_code=None,
    error_summary=None,
    execution_evidence=None,
    artifact_result=None,
    runtime=None,
    runtime_diagnostics=None,
    context=None,
    context_activation=None,
):
    response = {
        "protocol_version": job.protocol_version,
        "job_id": job.job_id,
        "actor": job.actor,
        "mode": job.mode,
        "workspace": job.workspace,
        "prompt_sha256": job.prompt_sha256,
        "status": status,
        "runtime": runtime or collect_runtime_evidence(job.actor),
    }
    if exit_code is not None:
        response["exit_code"] = exit_code
    if failure_class is not None:
        response["failure_class"] = failure_class
    if error_summary is not None:
        response["error_summary"] = error_summary
    if runtime_diagnostics is not None:
        response["runtime_diagnostics"] = runtime_diagnostics
    if context is not None:
        response["context"] = context
        if context.get("evidence_index") is not None:
            response["evidence_index"] = context["evidence_index"]
        if context.get("job_context") is not None:
            response["job_context"] = context["job_context"]
        if context.get("materialized_context") is not None:
            response["materialized_context"] = context["materialized_context"]
        if context.get("review_package") is not None:
            response["review_package"] = context["review_package"]
    if context_activation is not None:
        for key in ("context_activation_configured_mode", "context_activation_mode",
                    "context_activation_scope_status",
                    "context_activation_enforce_allowlist_count",
                    "context_activation_status",
                    "effective_input_sha256", "effective_input_bytes",
                    "actor_input_sha256", "context_payload_bytes",
                    "gate_reason_codes", "actor_started"):
            response[key] = context_activation.get(key)
        response["preflight"] = context_activation.get("preflight")
        snapshots = (context or {}).get("snapshots") if context is not None else None
        if context_activation.get("actor_started") and snapshots:
            response["post_actor_validation"] = snapshots.get(
                "post_actor_validation")
    if artifact_result is not None:
        (
            summary,
            artifact_status,
            artifacts,
            rejected,
            canonical_references,
        ) = unpack_artifact_result(artifact_result)
        response["summary"] = summary
    if execution_evidence is not None:
        before, after, changed_paths = execution_evidence
        response["git"] = {
            "baseline_commit": before["head"],
            "head_after": after["head"],
            "changed_paths": changed_paths,
        }
    if artifact_result is not None:
        response.update({
            "artifact_status": artifact_status,
            "artifacts": artifacts,
            "rejected_artifacts": rejected,
            "canonical_references": canonical_references,
        })
    if job.protocol_version == "3":
        response[
            "instruction_ref"
        ] = job.instruction_ref

        response[
            "instruction_sha256"
        ] = job.prompt_sha256
    return response


def _evidence_roots(workspace):
    roots = []
    if workspace.get("review_evidence_root"):
        roots.append({"path": workspace["review_evidence_root"], "kind": "review",
                      "manifest_name": "review-manifest.json"})
    if workspace.get("job_evidence_root"):
        roots.append({"path": workspace["job_evidence_root"], "kind": "historical_job",
                      "manifest_name": "job-evidence-manifest.json"})
    return roots


def publish_slack_result(log_dir, say, response):
    save_json(log_dir, "result.json", response)
    send_json(say, response)


def finalize_browser_callback(
    job,
    log_dir,
    response,
    *,
    status,
    artifact_status,
    failure_class=None,
):
    try:
        response["callback"] = send_browser_callback(
            job,
            status=status,
            artifact_status=artifact_status,
            failure_class=failure_class,
        )
    except Exception as exc:
        logger.exception("Browser callback failed: job_id=%s", job.job_id)
        response["callback"] = {
            "type": job.callback_type,
            "status": "FAILED",
            "error": str(exc)[:4000],
        }
    save_json(log_dir, "result.json", response)


def handle_execution_failure(
    job,
    say,
    log_dir,
    *,
    failure_class,
    response_status,
    artifact_status,
    error_summary=None,
    exception=None,
    context=None,
    context_activation=None,
):
    runtime_diagnostic = None
    if isinstance(exception, FileNotFoundError):
        runtime_diagnostic = classify_runtime_failure(error_summary, exception=exception)
        failure_class = runtime_diagnostic["classification"]
    state_store.mark_completed(
        job.job_id,
        status="FAILED",
        failure_class=failure_class,
    )
    response = build_result(
        job,
        status=response_status,
        failure_class=failure_class,
        error_summary=error_summary,
        runtime_diagnostics=runtime_diagnostic,
        context=context,
        context_activation=context_activation,
    )
    publish_slack_result(log_dir, say, response)
    finalize_browser_callback(
        job,
        log_dir,
        response,
        status=response_status,
        artifact_status=artifact_status,
        failure_class=failure_class,
    )


def log_job_end(job, status, exit_code):
    if getattr(_execution_context, "lifecycle_ended", None) == job.job_id:
        return
    _execution_context.lifecycle_ended = job.job_id
    emit_lifecycle(
        "\n==============================\n"
        f"JOB END\njob_id   : {job.job_id}\nactor    : {job.actor}\n"
        f"mode     : {job.mode}\nworkspace: {job.workspace}\nstatus   : {status}\n"
        f"exit_code: {exit_code}\n=============================="
    )
    _execution_context.lifecycle_started = None


def execute_job(job, say):
    if getattr(job, "operation", None) == TRUST_CONTROL_OPERATION:
        return execute_trust_control(job, say)
    if isinstance(job, Job) and job.actor == "claude" and job.mode == "review":
        return execute_claude_review(job, say)
    status = "RUNNING"
    exit_code = None
    workspace, workdir, log_dir, before = prepare_execution(job)
    context_session = begin_shadow(
        workdir, workspace=job.workspace, actor=job.actor, mode=job.mode,
        cache_root=Path(__file__).parent / "logs",
        evidence_roots=_evidence_roots(workspace),
        job=job,
    )
    activation = None

    try:
        preflight_context = None
        if (CONTEXT_HARNESS_ACTIVATION_MODE != "OFF" and
                has_context_request(job)):
            preflight_context = finish_shadow(
                workdir, context_session, job_id=job.job_id, before=before, after=before,
                attributable_changed_paths=[])
            # Retain the immutable input snapshot separately from the terminal scan.
            if context_session is not None:
                context_session["pre_actor_input"] = preflight_context
        resolution = resolve_activation_mode(
            CONTEXT_HARNESS_ACTIVATION_MODE,
            (context_session or {}).get("capability"),
            has_context_request(job),
            CONTEXT_HARNESS_ENFORCE_CAPABILITIES,
        )
        # Some internal legacy test/maintenance jobs predate resolved prompt storage.
        # Real accepted jobs always carry prompt; keep those helpers backward compatible.
        if hasattr(job, "prompt"):
            activation = prepare_context_activation(
                job, resolution.mode, preflight_context, workdir,
                configured_mode=CONTEXT_HARNESS_ACTIVATION_MODE,
                scope_status=resolution.scope_status,
                enforce_allowlist_count=len(CONTEXT_HARNESS_ENFORCE_CAPABILITIES))
        if activation is not None and activation["context_activation_status"] == "BLOCKED":
            status = "BLOCKED_CONTEXT"
            context = preflight_context
            state_store.mark_completed(
                job.job_id, status="FAILED", failure_class="BLOCKED_CONTEXT")
            response = build_result(
                job, status="BLOCKED_CONTEXT", failure_class="BLOCKED_CONTEXT",
                context=context, context_activation=activation)
            publish_slack_result(log_dir, say, response)
            finalize_browser_callback(
                job, log_dir, response, status="BLOCKED_CONTEXT",
                artifact_status="NOT_RUN", failure_class="BLOCKED_CONTEXT")
            return
        logger.info("ACTOR START: job_id=%s actor=%s", job.job_id, job.actor)
        if activation is not None:
            activation["actor_started"] = True
        if activation is not None and activation["context_activation_status"] == "INJECTED":
            result = run_agent(
                job, prompt=activation["actor_input"].decode("utf-8"), precomposed=True)
        else:
            result = run_agent(job)

        exit_code = result.returncode
        logger.info(
            "ACTOR END: job_id=%s actor=%s exit_code=%s",
            job.job_id, job.actor, exit_code,
        )

        after, changed_paths = collect_execution_evidence(
            result,
            workdir=workdir,
            log_dir=log_dir,
            before=before,
        )

        # -------------------------
        # Actor execution result
        # -------------------------

        if result.returncode != 0:
            status = "FAILED"
            logger.error("Actor failure: job_id=%s exit_code=%s", job.job_id, result.returncode)

            error_summary = (
                result.stderr.strip()
                or result.stdout.strip()
                or "Actor failed"
            )[-4000:]
            runtime_diagnostic = classify_runtime_failure(error_summary)
            failure_class = runtime_diagnostic["classification"]

            context = finish_shadow(
                workdir, context_session, job_id=job.job_id, before=before, after=after,
                attributable_changed_paths=get_attributable_changed_paths(before, after))
            state_store.mark_completed(
                job.job_id,
                status="FAILED",
                exit_code=result.returncode,
                failure_class=failure_class,
            )

            response = build_result(
                job,
                status="FAILED",
                failure_class=failure_class,
                exit_code=result.returncode,
                error_summary=error_summary,
                execution_evidence=(before, after, changed_paths),
                runtime=collect_runtime_evidence(job.actor, result),
                runtime_diagnostics=runtime_diagnostic,
                context=context,
                context_activation=activation,
            )
            publish_slack_result(log_dir, say, response)
            finalize_browser_callback(
                job,
                log_dir,
                response,
                status="FAILED",
                artifact_status="NOT_RUN",
                failure_class=failure_class,
            )

            return

        # -------------------------
        # Actor execution succeeded
        # -------------------------

        status = "DONE"

        artifact_result = process_artifacts(
            job,
            result,
            workspace,
            workdir,
            get_attributable_changed_paths(before, after),
        )
        (
            agent_summary,
            artifact_status,
            artifacts,
            rejected_artifacts,
            canonical_references,
        ) = unpack_artifact_result(artifact_result)

        context = finish_shadow(
            workdir, context_session, job_id=job.job_id, before=before, after=after,
            attributable_changed_paths=get_attributable_changed_paths(before, after))

        state_store.mark_completed(
            job.job_id,
            status="DONE",
            exit_code=result.returncode,
            failure_class=(
                None
                if artifact_status == "DONE"
                else "ARTIFACT_ERROR"
            ),
        )

        response = build_result(
            job,
            status="DONE",
            exit_code=result.returncode,
            execution_evidence=(before, after, changed_paths),
            artifact_result=(
                agent_summary,
                artifact_status,
                artifacts,
                rejected_artifacts,
                canonical_references,
            ),
            runtime=collect_runtime_evidence(job.actor, result),
            context=context,
            context_activation=activation,
        )
        publish_slack_result(log_dir, say, response)
        finalize_browser_callback(
            job,
            log_dir,
            response,
            status="DONE",
            artifact_status=artifact_status,
        )

    except subprocess.TimeoutExpired:
        status = "FAILED"

        context = finish_shadow(workdir, context_session, job_id=job.job_id)
        failure_args = dict(failure_class="TIMEOUT", response_status="FAILED",
                            artifact_status="NOT_RUN")
        if context is not None:
            failure_args["context"] = context
        if activation is not None:
            failure_args["context_activation"] = activation
        handle_execution_failure(job, say, log_dir, **failure_args)

    except Exception as e:
        status = "FAILED"

        context = finish_shadow(workdir, context_session, job_id=job.job_id)

        failure_args = dict(
            failure_class="BRIDGE_ERROR", response_status="BRIDGE_ERROR",
            artifact_status="UNKNOWN", error_summary=str(e)[:4000], exception=e)
        if context is not None:
            failure_args["context"] = context
        if activation is not None:
            failure_args["context_activation"] = activation
        handle_execution_failure(job, say, log_dir, **failure_args)

        logger.exception("BRIDGE ERROR: job_id=%s", job.job_id)

    finally:
        log_job_end(job, status, exit_code)

        if not getattr(_execution_context, "claimed", False):
            dispatch_next_queued(job.workspace, say)


def execute_claude_review(job, say):
    """Worker-owned preparation, execution, boundary proof and adoption."""
    status = "FAILED"
    exit_code = None
    workspace = WORKSPACES[job.workspace]
    canonical = workspace["path"]
    log_dir = None
    review = None
    actor_status = "NOT_STARTED"
    failure_class = "BRIDGE_ERROR"
    result = None
    boundary = None
    head_after = None
    outputs = []
    final_text = None
    normalized = []
    cleanup_status = "PENDING"
    artifact_result = None
    artifact_status = "NOT_RUN"
    error_summary = None
    runtime_diagnostic = None
    response = None
    review_evidence = not_run_review_evidence(bool(workspace.get("review_evidence_root")))
    context_session = None
    context = None
    review_launch = None

    try:
        log_dir = create_job_log(job.job_id)
        workspace, canonical, log_dir, _ = prepare_execution(job, log_dir=log_dir)
        context_session = begin_shadow(
            canonical, workspace=job.workspace, actor=job.actor, mode=job.mode,
            cache_root=Path(__file__).parent / "logs",
            evidence_roots=_evidence_roots(workspace),
            job=job,
        )
        review_launch = prepare_review_invocation(
            job, canonical, workspace, context_session
        )
        review = create_review_workspace(canonical, job.job_id)

        save_json(log_dir, "review-input.json", review.input_manifest)
        save_text(log_dir, "canonical-diff-head-before.stat", review.canonical_diff_stat)
        save_text(log_dir, "review-diff-head-before.stat", review.review_diff_stat)
        effective_prompt, effective_prompt_sha256 = prepare_effective_prompt(
            job, review_launch, (context_session or {}).get("capability")
        )
        try:
            logger.info("ACTOR START: job_id=%s actor=%s", job.job_id, job.actor)
            result = claude.run(
                job, prompt=effective_prompt, workdir=review.root,
                settings_path=review.settings_path,
            )
            exit_code = result.returncode
            logger.info("ACTOR END: job_id=%s actor=%s exit_code=%s",
                        job.job_id, job.actor, exit_code)
            actor_status = "DONE" if result.returncode == 0 else "AGENT_ERROR"
            if actor_status != "DONE":
                failure_class = "ACTOR_FAILED"
        except subprocess.TimeoutExpired as exc:
            actor_status = "TIMEOUT"
            failure_class = "TIMEOUT"
            result = getattr(exc, "result", None)
        except Exception as exc:
            actor_status = "AGENT_ERROR"
            failure_class = "ACTOR_FAILED"
            result = ProcessResult(1, "", str(exc))

        # run_process settles its owned process tree before returning/raising.
        boundary, _, head_after, outputs = finish_review(review)
        save_text(log_dir, "canonical-diff-head-after.stat", diff_head_stat(canonical))
        save_text(log_dir, "review-diff-head-after.stat", diff_head_stat(review.root))
        if result is not None:
            save_text(log_dir, "claude-stream.jsonl", result.stdout)
            save_text(log_dir, "stderr.txt", result.stderr)
            final_text, normalized = parse_stream_json(result.stdout)
            capability = (context_session or {}).get("capability")
            decision, decision_error = parse_block(
                final_text, review_job_id=job.job_id, workspace=job.workspace,
                capability=capability,
            )
            if decision is not None:
                save_json(log_dir, "review-decision.json", decision)
            elif decision_error:
                save_json(log_dir, "review-decision-quarantine.json", {
                    "status": "REJECTED", "error": decision_error,
                    "review_job_id": job.job_id,
                })
            save_json(log_dir, "review-execution.json", {
                "events": normalized, "final_result_text": final_text,
                "exit_code": result.returncode,
                "review_context": review_telemetry(
                    review_launch, normalized, result.stdout, final_text,
                    instruction_sha256=job.prompt_sha256,
                    effective_prompt_sha256=effective_prompt_sha256,
                ),
                "review_decision": {
                    "status": "VALID" if decision is not None else
                              "REJECTED" if decision_error else "MISSING",
                    "path": "review-decision.json" if decision is not None else None,
                    "error": decision_error,
                    "finding_count": len(decision["findings"]) if decision else 0,
                    "trust": "ACTOR_REPORTED" if decision else None,
                },
            })
        transcript_persisted = result is not None
        evidence_persisted = transcript_persisted and final_text is not None
        adoptable = actor_status == "DONE" and boundary == "CLEAN" and evidence_persisted
        status = "DONE" if actor_status == "DONE" else "FAILED"
        if boundary != "CLEAN" and actor_status == "DONE":
            failure_class = boundary
        elif actor_status == "DONE" and not evidence_persisted:
            failure_class = "EXECUTION_EVIDENCE_MISSING"
        elif actor_status == "DONE":
            failure_class = None
        elif actor_status == "AGENT_ERROR":
            error_summary = (
                (result.stderr.strip() or result.stdout.strip())
                if result is not None else "Actor failed"
            )[-4000:]
            runtime_diagnostic = classify_runtime_failure(error_summary)
            failure_class = runtime_diagnostic["classification"]

        # A non-CLEAN clone is evidence, never an authoritative artifact source.
        if adoptable:
            bridge_result = ProcessResult(result.returncode, final_text, result.stderr)
            artifact_result = process_artifacts(job, bridge_result, workspace, review.root)
            artifact_status = artifact_result[1]

    except PackageLaunchError as exc:
        failure_class = "PACKAGE_PRELAUNCH_REJECTED"
        error_summary = str(exc)[:4000]
        review_launch = {"mode": getattr(job, "review_mode", None) or "FULL_REVIEW",
                         "ref": getattr(job, "review_package_ref", None),
                         "prelaunch_failed": True, "error": error_summary,
                         "current_context_sha256": ((context_session or {}).get("pre") or {}).get(
                             "lifecycle", {}).get("manifest_sha256")}
    except ValueError as exc:
        if (review_launch is None and
                getattr(job, "review_mode", None) == "DELTA_REVIEW" and
                getattr(job, "review_package_ref", None) is not None):
            failure_class = "PACKAGE_PRELAUNCH_REJECTED"
            error_summary = str(exc)[:4000]
            review_launch = {"mode": "DELTA_REVIEW", "ref": job.review_package_ref,
                             "prelaunch_failed": True, "error": error_summary,
                             "current_context_sha256": ((context_session or {}).get("pre") or {}).get(
                                 "lifecycle", {}).get("manifest_sha256")}
        else:
            failure_class = "BRIDGE_ERROR"
            error_summary = str(exc)[:4000]
            logger.exception("CLAUDE REVIEW BRIDGE ERROR: job_id=%s", job.job_id)
    except ReviewPreparationError as exc:
        failure_class = exc.failure_class
        error_summary = str(exc)[:4000]
    except Exception as exc:
        # All post-acceptance bridge faults converge through the same terminal
        # closure below, including evidence parsing and boundary verification.
        failure_class = "BRIDGE_ERROR"
        error_summary = str(exc)[:4000]
        logger.exception("CLAUDE REVIEW BRIDGE ERROR: job_id=%s", job.job_id)

    try:
        transcript_persisted = result is not None
        evidence_persisted = transcript_persisted and final_text is not None
        adoptable = actor_status == "DONE" and boundary == "CLEAN" and evidence_persisted
        # Canonical evidence adoption and the mandatory scan both occur while the
        # DISPATCHING/RUNNING workspace claim is still held.
        context = finish_shadow(canonical, context_session, job_id=job.job_id)
        response = build_result(
            job, status=status, failure_class=failure_class, exit_code=exit_code,
            error_summary=error_summary, artifact_result=artifact_result,
            runtime=collect_runtime_evidence(job.actor, result),
            runtime_diagnostics=runtime_diagnostic,
            context=context,
        )
        response.update({
            "review_input": None,
            "review_boundary": {"status": boundary} if boundary is not None else None,
            "review_execution": {
                "actor_status": actor_status,
                "raw_transcript": "claude-stream.jsonl" if result is not None else None,
                "normalized_evidence": "review-execution.json" if result is not None else None,
                "partial_evidence_available": transcript_persisted and actor_status == "TIMEOUT",
                "evidence_persisted": evidence_persisted,
                "adoptable": adoptable,
            },
            "review_workspace": None,
            "cleanup_status": "PENDING",
        })
        response["review_context"] = review_telemetry(
            review_launch, normalized, result.stdout if result is not None else "", final_text,
            instruction_sha256=job.prompt_sha256,
            effective_prompt_sha256=(
                effective_prompt_sha256 if "effective_prompt_sha256" in locals() else None
            ),
        )
        if review is not None:
            response.update({
                "canonical_head": review.canonical_before["head"],
                "input_manifest_sha256": review.input_manifest["input_manifest_sha256"],
                "file_count": review.input_manifest["file_count"],
                "review_input": {
                    "version": 1,
                    "canonical_head": review.canonical_before["head"],
                    "input_manifest_sha256": review.input_manifest["input_manifest_sha256"],
                    "file_count": review.input_manifest["file_count"],
                    "canonical_diff_head_stat": "canonical-diff-head-before.stat",
                    "review_diff_head_stat": "review-diff-head-before.stat",
                },
                "review_workspace": {
                    "type": "independent_clone", "head_before": review.head_before,
                    "head_after": head_after, "outputs": outputs,
                },
            })
        if actor_status == "DONE" and boundary != "CLEAN":
            response["artifact_status"] = "NOT_RUN"
            response["artifact_skip_reason"] = (
                f"Review artifacts are non-authoritative because boundary is {boundary}."
            )
        if adoptable and review is not None:
            try:
                review_evidence = adopt_review_evidence(
                    canonical=canonical,
                    review_evidence_root=workspace.get("review_evidence_root"),
                    job_id=job.job_id,
                    log_dir=log_dir,
                    actor=job.actor,
                    mode=job.mode,
                    canonical_head=review.canonical_before["head"],
                    review_head_before=review.head_before,
                    review_head_after=head_after,
                    input_manifest_sha256=review.input_manifest["input_manifest_sha256"],
                    file_count=review.input_manifest["file_count"],
                    review_boundary=boundary,
                    actor_status=actor_status,
                    evidence_persisted=evidence_persisted,
                    adoptable=adoptable,
                    review_context=response["review_context"],
                )
            except Exception as exc:
                # Evidence delivery is deliberately not a review qualification
                # domain.  Preserve DONE/CLEAN/adoptable on any transport fault.
                logger.exception("REVIEW EVIDENCE ADOPTION FAILED: job_id=%s", job.job_id)
                review_evidence = failed_review_evidence(exc)
        response["review_evidence"] = review_evidence
        # Adoption precedes this refresh so the current canonical package is
        # visible.  Index failure is diagnostic-only in Phase 2A.
        evidence_index = refresh_evidence_index(canonical, context_session)
        if evidence_index is not None:
            response["evidence_index"] = evidence_index
            if context is not None:
                context["evidence_index"] = evidence_index
    except Exception as exc:
        status = "FAILED"
        failure_class = "RESULT_ASSEMBLY_FAILED"
        artifact_status = "NOT_RUN"
        context = finish_shadow(canonical, context_session, job_id=job.job_id)
        response = build_result(job, status=status, failure_class=failure_class,
                                error_summary=str(exc)[:4000], context=context)
        response.update({"review_input": None, "review_boundary": None,
                         "review_execution": None, "review_workspace": None,
                         "review_evidence": review_evidence,
                         "cleanup_status": "PENDING"})

    # Terminal publication is exactly once.  Each sink is isolated so a failed
    # DB/local/Slack write cannot suppress the callback attempt or cleanup.
    db_persisted = False
    try:
        state_store.mark_completed(job.job_id, status=status, exit_code=exit_code,
                                   failure_class=failure_class)
        db_persisted = True
    except Exception:
        logger.exception("Unable to persist terminal DB state: job_id=%s", job.job_id)
    try:
        if log_dir is not None:
            publish_slack_result(log_dir, say, response)
        else:
            send_json(say, response)
    except Exception:
        logger.exception("Unable to publish terminal Slack Result: job_id=%s", job.job_id)
    try:
        if log_dir is not None:
            finalize_browser_callback(job, log_dir, response, status=status,
                                      artifact_status=artifact_status,
                                      failure_class=failure_class)
        else:
            response["callback"] = send_browser_callback(
                job, status=status, artifact_status=artifact_status,
                failure_class=failure_class,
            )
    except Exception:
        logger.exception("Unable to attempt terminal callback: job_id=%s", job.job_id)

    if review is not None:
        try:
            cleanup_status = cleanup_review(
                review,
                outcome_path=(log_dir / "cleanup.json") if log_dir is not None else None,
            )
        except Exception:
            cleanup_status = "FAILED"
            logger.exception("Review workspace cleanup failed: job_id=%s", job.job_id)
    response["cleanup_status"] = cleanup_status
    if not db_persisted:
        try:
            state_store.mark_completed(job.job_id, status=status, exit_code=exit_code,
                                       failure_class=failure_class)
            db_persisted = True
        except Exception:
            logger.exception("Terminal DB state retry failed: job_id=%s", job.job_id)
    try:
        if log_dir is not None:
            save_json(log_dir, "result.json", response)
    except Exception:
        logger.exception("Unable to persist final Result: job_id=%s", job.job_id)

    logger.info("Claude review end: job_id=%s status=%s actor=%s boundary=%s cleanup=%s",
                job.job_id, status, actor_status, boundary, cleanup_status)
    log_job_end(job, status, exit_code)
    if not getattr(_execution_context, "claimed", False):
        dispatch_next_queued(job.workspace, say)


def process_exists(pid):
    if pid is None:
        return None

    try:
        result = subprocess.run(
            [
                "tasklist",
                "/FI",
                f"PID eq {pid}",
                "/FO",
                "CSV",
                "/NH",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )

        if result.returncode != 0:
            return None

        output = result.stdout.strip()

        if not output:
            return False

        # 該当PIDがなければ tasklist は
        # INFO: No tasks are running...
        # のような文字列を返す。
        if output.upper().startswith("INFO:"):
            return False

        # CSV結果のPID列を確認
        return f'"{pid}"' in output

    except Exception:
        return None


def recover_running_jobs():
    running = state_store.list_running()

    if not running:
        return

    logger.warning("Crash recovery: %d RUNNING job(s)", len(running))

    for row in running:
        job_id = row["job_id"]
        host = row["host"]
        pid = row["pid"]

        # 別hostのPIDはこのWorkerから
        # 安全に判定できない
        if host != HOSTNAME:
            state_store.mark_recovery_required(
                job_id
            )

            logger.warning("RECOVERY_REQUIRED: job_id=%s host mismatch", job_id)
            continue

        alive = process_exists(pid)

        if alive is False:
            state_store.mark_interrupted(
                job_id
            )

            logger.warning("INTERRUPTED: job_id=%s process not found", job_id)

        else:
            # process alive または
            # 状態を確定できない。
            #
            # 再起動したWorkerは既存processを
            # 管理下へ安全に再attachできないため、
            # 自動継続扱いにはしない。
            state_store.mark_recovery_required(
                job_id
            )

            logger.warning("RECOVERY_REQUIRED: job_id=%s pid=%s", job_id, pid)


def main(argv=()):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", nargs="?", const="all",
        choices=("all", "codex", "claude"),
    )
    parser.add_argument("--adopt-job-evidence", metavar="JOB_ID")
    parser.add_argument("--workspace")
    parser.add_argument("--historical-manual", action="store_true")
    parser.add_argument("--human-approved", action="store_true")
    parser.add_argument("--slack-result-manifest", metavar="JSON_FILE")
    parser.add_argument("--adopt-review-decision", metavar="SOURCE_JOB_ID")
    parser.add_argument("--decision-mapping", metavar="JSON_FILE")
    args = parser.parse_args(argv)
    if args.check:
        actors = ("codex", "claude") if args.check == "all" else (args.check,)
        return run_checks(actors)

    if args.adopt_review_decision:
        if (not args.workspace or not args.historical_manual or not args.human_approved
                or not args.decision_mapping or args.workspace not in WORKSPACES):
            result={"status":"FAILED","mode":"HISTORICAL_MANUAL",
                    "source_job_id":args.adopt_review_decision,
                    "error":"--workspace, --historical-manual, --human-approved and --decision-mapping are required"}
        else:
            wc=WORKSPACES[args.workspace]; state_store.initialize()
            owner=f"historical-review:{args.adopt_review_decision}"
            if not state_store.acquire_manual_workspace_claim(args.workspace,owner):
                result={"status":"FAILED","mode":"HISTORICAL_MANUAL",
                        "source_job_id":args.adopt_review_decision,"error":"WORKSPACE_BUSY"}
            else:
                try:
                    result=adopt_historical_review_decision(
                        canonical=wc["path"], evidence_root=wc.get("review_evidence_root"),
                        source_job_id=args.adopt_review_decision, workspace=args.workspace,
                        log_dir=Path(__file__).parent/"logs"/args.adopt_review_decision,
                        mapping_path=args.decision_mapping, human_approved=True)
                finally:
                    state_store.release_manual_workspace_claim(args.workspace,owner)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return 0 if result["status"] in ("ADOPTED","NOOP") else 1

    if args.adopt_job_evidence:
        if not args.workspace or not args.historical_manual or not args.slack_result_manifest:
            result = {
                "status": "FAILED", "mode": "HISTORICAL_MANUAL",
                "job_id": args.adopt_job_evidence, "workspace": args.workspace,
                "destination": None, "manifest_sha256": None,
                "corroboration": None,
                "trust_limitation": (
                    "Historical evidence lacks an original terminal-time cryptographic anchor."
                ),
                "error": ("--workspace, --historical-manual, and "
                          "--slack-result-manifest are required"),
            }
        elif args.workspace not in WORKSPACES:
            result = {
                "status": "FAILED", "mode": "HISTORICAL_MANUAL",
                "job_id": args.adopt_job_evidence, "workspace": args.workspace,
                "destination": None, "manifest_sha256": None,
                "corroboration": None,
                "trust_limitation": (
                    "Historical evidence lacks an original terminal-time cryptographic anchor."
                ),
                "error": "unknown workspace",
            }
        else:
            workspace_config = WORKSPACES[args.workspace]
            state_store.initialize()
            owner = f"historical:{args.adopt_job_evidence}"
            if not state_store.acquire_manual_workspace_claim(args.workspace, owner):
                result = {
                    "status": "FAILED", "mode": "HISTORICAL_MANUAL",
                    "job_id": args.adopt_job_evidence, "workspace": args.workspace,
                    "destination": None, "manifest_sha256": None,
                    "corroboration": None,
                    "trust_limitation": (
                        "Historical evidence lacks an original terminal-time cryptographic anchor."
                    ),
                    "error": "WORKSPACE_BUSY",
                }
            else:
                try:
                    result = adopt_historical_job_evidence(
                        canonical=workspace_config["path"],
                        evidence_root=workspace_config.get("job_evidence_root"),
                        job_id=args.adopt_job_evidence,
                        workspace=args.workspace,
                        log_dir=Path(__file__).parent / "logs" / args.adopt_job_evidence,
                        state_row=state_store.get_job(args.adopt_job_evidence),
                        slack_manifest_path=args.slack_result_manifest,
                        human_approved=args.human_approved,
                    )
                finally:
                    state_store.release_manual_workspace_claim(args.workspace, owner)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] in ("ADOPTED", "NOOP") else 1

    configure_logging()
    state_store.initialize()

    cli_available = check_cli_versions()

    try:
        guard = WorkerInstanceGuard().acquire()
    except SingleInstanceAlreadyRunning as exc:
        logger.error(str(exc))
        return 2

    with guard:
        recover_running_jobs()

        bridge = SlackBridge(
            process_message
        )

        recover_queued_jobs(bridge.say)

        logger.info("ChatGPT Local Agent Worker started.")
        logger.info("Authorization and state store enabled.")
        logger.info("Press Ctrl+C to stop.")

        bridge.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
