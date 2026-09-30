import json
import hashlib
import logging
import subprocess
import threading

from actors import run_agent
from config import HOSTNAME
from job_protocol import (
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

        send_json(
            say,
            {
                "status": "BRIDGE_ERROR",
                "failure_class": "INVALID_JOB",
                "error_summary": str(e),
            },
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

            response = {
                    "protocol_version":
                        job.protocol_version,
                    "job_id":
                        job.job_id,
                    "status":
                        "BRIDGE_ERROR",
                    "failure_class":
                        failure_class,
                    "error_summary":
                        failure_class,
                }
            send_json(say, response)
            send_browser_callback(
                job,
                status="BRIDGE_ERROR",
                artifact_status="NOT_RUN",
                failure_class=failure_class,
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


def prepare_execution(job):
    workspace = WORKSPACES[job.workspace]
    workdir = workspace["path"]
    log_dir = create_job_log(job.job_id)

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
):
    response = {
        "protocol_version": job.protocol_version,
        "job_id": job.job_id,
        "actor": job.actor,
        "mode": job.mode,
        "workspace": job.workspace,
        "prompt_sha256": job.prompt_sha256,
        "status": status,
    }
    if exit_code is not None:
        response["exit_code"] = exit_code
    if failure_class is not None:
        response["failure_class"] = failure_class
    if error_summary is not None:
        response["error_summary"] = error_summary
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
):
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


def execute_job(job, say):
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

            state_store.mark_completed(
                job.job_id,
                status="FAILED",
                exit_code=result.returncode,
                failure_class="ACTOR_FAILED",
            )

            error_summary = (
                result.stderr.strip()
                or result.stdout.strip()
                or "Actor failed"
            )[-4000:]
            response = build_result(
                job,
                status="FAILED",
                failure_class="ACTOR_FAILED",
                exit_code=result.returncode,
                error_summary=error_summary,
                execution_evidence=(before, after, changed_paths),
            )
            publish_slack_result(log_dir, say, response)
            finalize_browser_callback(
                job,
                log_dir,
                response,
                status="FAILED",
                artifact_status="NOT_RUN",
                failure_class="ACTOR_FAILED",
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
        )

        logger.exception("BRIDGE ERROR: job_id=%s", job.job_id)

    finally:
        logger.info(
            "\n==============================\n"
            "JOB END\njob_id   : %s\nactor    : %s\nmode     : %s\n"
            "workspace: %s\nstatus   : %s\nexit_code: %s\n"
            "==============================",
            job.job_id, job.actor, job.mode, job.workspace, status, exit_code,
        )

        dispatch_next_queued(
            job.workspace,
            say,
        )


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


def main():
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


if __name__ == "__main__":
    main()
