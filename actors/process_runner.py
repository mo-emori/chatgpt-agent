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
        bufsize=1,
    )

    state_store.mark_running(
        job.job_id,
        process.pid,
        HOSTNAME,
    )

    stdout_lines = []
    stderr_lines = []

    def read_stream(
        stream,
        collector,
        label,
    ):
        try:
            for line in iter(
                stream.readline,
                "",
            ):
                collector.append(line)

                print(
                    f"[{label}] "
                    f"{line.rstrip()}",
                    flush=True,
                )
        finally:
            stream.close()

    stdout_thread = threading.Thread(
        target=read_stream,
        args=(
            process.stdout,
            stdout_lines,
            "AGENT",
        ),
        daemon=True,
    )

    stderr_thread = threading.Thread(
        target=read_stream,
        args=(
            process.stderr,
            stderr_lines,
            "AGENT-ERR",
        ),
        daemon=True,
    )

    stdout_thread.start()
    stderr_thread.start()

    # stdinへPromptを渡したら閉じる。
    if input_text is not None:
        process.stdin.write(
            input_text
        )
        process.stdin.close()

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
        process.wait(
            timeout=timeout
        )

    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

        raise

    finally:
        stop_heartbeat.set()

        heartbeat_thread.join(
            timeout=2
        )

        stdout_thread.join(
            timeout=5
        )

        stderr_thread.join(
            timeout=5
        )

    return ProcessResult(
        returncode=process.returncode,
        stdout="".join(
            stdout_lines
        ),
        stderr="".join(
            stderr_lines
        ),
    )