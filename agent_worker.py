import json
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
        return {
            "type":
                job.callback_type,
            "status": "FAILED",
            "error":
                "UNKNOWN_CALLBACK_TYPE",
        }

    message = (
        "LOCAL_AGENT_JOB_COMPLETED\n\n"
        f"job_id: {job.job_id}\n"
        f"actor: {job.actor}\n"
        f"workspace: {job.workspace}\n"
        f"status: {status}\n"
        f"artifact_status: "
        f"{artifact_status}\n\n"
        "Slack Result Manifestを確認して"
        "Job Closureを続行してください。"
    )

    try:
        notify_chatgpt(
            target_url=
                job.callback_url,
            message=message,
        )

        return {
            "type":
                "chatgpt_browser",
            "status":
                "DONE",
            "url": job.callback_url,
        }

    except BrowserNotifyError as e:
        print(
            "BROWSER CALLBACK FAILED:",
            str(e),
        )

        return {
            "type":
                "chatgpt_browser",
            "status":
                "FAILED",
            "error":
                str(e),
            "url": job.callback_url,
        }


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
        print("JOB validation failed:", e)

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
        print(
            "Duplicate JOB ignored:",
            job.job_id,
            existing["status"],
        )

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

    # RECEIVED
    state_store.create_job(job)

    # JOB validation completed
    state_store.set_status(
        job.job_id,
        "VALIDATED",
    )

    if state_store.is_workspace_busy(
        job.workspace
    ):
        state_store.mark_queued(
            job.job_id
        )

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

        print(
            "JOB QUEUED:",
            job.job_id,
        )
        return

    # workspaceが空いているので非同期実行
    threading.Thread(
        target=execute_job,
        args=(job, say),
        daemon=True,
    ).start()

    return


def dispatch_next_queued(workspace, say):
    if state_store.is_workspace_busy(workspace):
        return

    row = state_store.get_next_queued(workspace)

    if row is None:
        return

    job = job_from_row(row)

    print(
        "DISPATCH QUEUED JOB:",
        job.job_id,
    )

    threading.Thread(
        target=execute_job,
        args=(job, say),
        daemon=True,
    ).start()


def prepare_execution(job):
    workspace = WORKSPACES[job.workspace]
    workdir = workspace["path"]
    log_dir = create_job_log(job.job_id)

    save_json(
        log_dir,
        "request.json",
        {
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
        },
    )

    before = get_git_snapshot(workdir)
    save_git_snapshot(log_dir, "before", before)

    print()
    print("==============================")
    print("JOB START")
    print("job_id   :", job.job_id)
    print("actor    :", job.actor)
    print("mode     :", job.mode)
    print("workspace:", job.workspace)
    print("baseline :", before["head"])
    print("==============================")

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
        artifact_status = "FAILED"
        rejected_artifacts.append({
            "path": None,
            "reason": f"Manifest error: {e}",
        })
    except Exception as e:
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
):
    response["callback"] = send_browser_callback(
        job,
        status=status,
        artifact_status=artifact_status,
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

        print(
            "BRIDGE ERROR:",
            job.job_id,
            repr(e),
        )

    finally:
        print()
        print("==============================")
        print("JOB END")
        print("job_id   :", job.job_id)
        print("actor    :", job.actor)
        print("mode     :", job.mode)
        print("workspace:", job.workspace)
        print("status   :", status)
        print("exit_code:", exit_code)
        print("==============================")

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

    print(
        f"Crash recovery: "
        f"{len(running)} RUNNING job(s)"
    )

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

            print(
                "RECOVERY_REQUIRED:",
                job_id,
                "host mismatch",
            )
            continue

        alive = process_exists(pid)

        if alive is False:
            state_store.mark_interrupted(
                job_id
            )

            print(
                "INTERRUPTED:",
                job_id,
                "process not found",
            )

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

            print(
                "RECOVERY_REQUIRED:",
                job_id,
                f"pid={pid}",
            )


def main():
    state_store.initialize()

    cli_available = check_cli_versions()

    recover_running_jobs()

    print(
        "ChatGPT Local Agent Worker started."
    )
    print(
        "Authorization and state store enabled."
    )
    print("Press Ctrl+C to stop.")

    bridge = SlackBridge(
        process_message
    )

    bridge.start()


if __name__ == "__main__":
    main()
