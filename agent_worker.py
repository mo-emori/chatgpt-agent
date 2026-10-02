import json
import hashlib
import logging
import subprocess
import threading
import argparse
import sys
from pathlib import Path

from actors import run_agent
from actors import claude
from actors.claude_stream import parse_stream_json
from actors.process_runner import ProcessResult
from actors.review_workspace import (
    ReviewPreparationError, cleanup_review, create_review_workspace, diff_head_stat, finish_review,
)
from config import HOSTNAME
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
from historical_job_evidence import adopt_historical_job_evidence
from job_log import (
    create_job_log,
    get_changed_paths,
    get_git_snapshot,
    save_git_snapshot,
    save_json,
    save_text,
)
from browser.notify import (
    BrowserNotifyError,
    notify_chatgpt,
)
from dataclasses import replace
from notion_client import (
    NotionInstructionError,
    fetch_instruction,
)
from operational_logging import configure_logging

logger = logging.getLogger(__name__)

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
        notify_chatgpt(
            target_url=
                job.callback_url,
            message=message,
        )
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

    if job.protocol_version == "3":
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
        send_browser_callback(
            job,
            status="FAILED",
            artifact_status="NOT_RUN",
            failure_class="DISPATCH_START_FAILED",
        )
        logger.exception("DISPATCH START FAILED: job_id=%s", job.job_id)
        dispatch_next_queued(job.workspace, say)


def validate_recoverable_queued_job(job):
    if job.protocol_version != "3":
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

    save_json(
        log_dir,
        "request.json",
        request_data,
    )

    before = get_git_snapshot(workdir)
    save_git_snapshot(log_dir, "before", before)

    logger.info(
        "\n==============================\n"
        "JOB START\njob_id   : %s\nactor    : %s\nmode     : %s\n"
        "workspace: %s\nbaseline : %s\n==============================",
        job.job_id, job.actor, job.mode, job.workspace, before["head"],
    )

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


def process_artifacts(job, result, workspace, workdir):
    artifact_status = "DONE"
    artifacts = []
    rejected_artifacts = []
    agent_summary = result.stdout.strip()

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
    )


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
    if artifact_result is not None:
        summary, artifact_status, artifacts, rejected = artifact_result
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
        })
    if job.protocol_version == "3":
        response[
            "instruction_ref"
        ] = job.instruction_ref

        response[
            "instruction_sha256"
        ] = job.prompt_sha256
    return response


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
    response["callback"] = send_browser_callback(
        job,
        status=status,
        artifact_status=artifact_status,
        failure_class=failure_class,
    )
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
    logger.info(
        "\n==============================\n"
        "JOB END\njob_id   : %s\nactor    : %s\nmode     : %s\n"
        "workspace: %s\nstatus   : %s\nexit_code: %s\n"
        "==============================",
        job.job_id, job.actor, job.mode, job.workspace, status, exit_code,
    )


def execute_job(job, say):
    if isinstance(job, Job) and job.actor == "claude" and job.mode == "review":
        return execute_claude_review(job, say)
    status = "RUNNING"
    exit_code = None
    workspace, workdir, log_dir, before = prepare_execution(job)

    try:
        result = run_agent(job)

        exit_code = result.returncode

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

        (
            agent_summary,
            artifact_status,
            artifacts,
            rejected_artifacts,
        ) = process_artifacts(
            job,
            result,
            workspace,
            workdir,
        )

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
            ),
            runtime=collect_runtime_evidence(job.actor, result),
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

        handle_execution_failure(
            job,
            say,
            log_dir,
            failure_class="TIMEOUT",
            response_status="FAILED",
            artifact_status="NOT_RUN",
        )

    except Exception as e:
        status = "FAILED"

        handle_execution_failure(
            job,
            say,
            log_dir,
            failure_class="BRIDGE_ERROR",
            response_status="BRIDGE_ERROR",
            artifact_status="UNKNOWN",
            error_summary=str(e)[:4000],
            exception=e,
        )

        logger.exception("BRIDGE ERROR: job_id=%s", job.job_id)

    finally:
        log_job_end(job, status, exit_code)

        dispatch_next_queued(
            job.workspace,
            say,
        )


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

    try:
        log_dir = create_job_log(job.job_id)
        workspace, canonical, log_dir, _ = prepare_execution(job, log_dir=log_dir)
        review = create_review_workspace(canonical, job.job_id)

        save_json(log_dir, "review-input.json", review.input_manifest)
        save_text(log_dir, "canonical-diff-head-before.stat", review.canonical_diff_stat)
        save_text(log_dir, "review-diff-head-before.stat", review.review_diff_stat)
        try:
            result = claude.run(job, workdir=review.root, settings_path=review.settings_path)
            exit_code = result.returncode
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
            save_json(log_dir, "review-execution.json", {
                "events": normalized, "final_result_text": final_text,
                "exit_code": result.returncode,
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
        response = build_result(
            job, status=status, failure_class=failure_class, exit_code=exit_code,
            error_summary=error_summary, artifact_result=artifact_result,
            runtime=collect_runtime_evidence(job.actor, result),
            runtime_diagnostics=runtime_diagnostic,
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
                )
            except Exception as exc:
                # Evidence delivery is deliberately not a review qualification
                # domain.  Preserve DONE/CLEAN/adoptable on any transport fault.
                logger.exception("REVIEW EVIDENCE ADOPTION FAILED: job_id=%s", job.job_id)
                review_evidence = failed_review_evidence(exc)
        response["review_evidence"] = review_evidence
    except Exception as exc:
        status = "FAILED"
        failure_class = "RESULT_ASSEMBLY_FAILED"
        artifact_status = "NOT_RUN"
        response = build_result(job, status=status, failure_class=failure_class,
                                error_summary=str(exc)[:4000])
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
    args = parser.parse_args(argv)
    if args.check:
        actors = ("codex", "claude") if args.check == "all" else (args.check,)
        return run_checks(actors)

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
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] in ("ADOPTED", "NOOP") else 1

    configure_logging()
    state_store.initialize()

    cli_available = check_cli_versions()

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
