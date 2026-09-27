import subprocess
import threading
import time
from dataclasses import dataclass

import state_store
from config import (
    HEARTBEAT_INTERVAL_SECONDS,
    HOSTNAME,
)

@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def validate_workspace(config):
    workdir = config["path"]

    if not workdir.is_dir():
        raise RuntimeError(
            f"Workspace not found: {workdir}"
        )

    if config["git_required"]:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(workdir),
                "rev-parse",
                "--is-inside-work-tree",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if (
            result.returncode != 0
            or result.stdout.strip() != "true"
        ):
            raise RuntimeError(
                f"Git repository required: {workdir}"
            )


def run_process(
    *,
    job,
    args,
    cwd,
    timeout,
    input_text=None,
):
    process = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=(
            subprocess.PIPE
            if input_text is not None
            else None
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    state_store.mark_running(
        job.job_id,
        process.pid,
        HOSTNAME,
    )

    stop_heartbeat = threading.Event()

    def heartbeat_loop():
        while not stop_heartbeat.wait(
            HEARTBEAT_INTERVAL_SECONDS
        ):
            state_store.heartbeat(
                job.job_id
            )

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        stdout, stderr = process.communicate(
            input=input_text,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise

    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2)

    return ProcessResult(
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )