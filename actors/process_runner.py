import logging
import subprocess
import threading
from dataclasses import dataclass

import state_store
from actors.environment import build_actor_env
from actors.process_tree import ProcessTree
from config import HEARTBEAT_INTERVAL_SECONDS, HOSTNAME

logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


class ProcessTimeoutExpired(subprocess.TimeoutExpired):
    """Timeout carrying output already collected by the stream readers."""

    def __init__(self, cmd, timeout, result):
        super().__init__(cmd, timeout, output=result.stdout, stderr=result.stderr)
        self.result = result


def validate_workspace(config):
    workdir = config["path"]
    if not workdir.is_dir():
        raise RuntimeError(f"Workspace not found: {workdir}")
    if config["git_required"]:
        result = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            raise RuntimeError(f"Git repository required: {workdir}")


def run_process(*, job, args, cwd, timeout, input_text=None, actor=None):
    actor_env = build_actor_env(actor or job.actor)
    secret_values = {
        value for key, value in actor_env.items()
        if value and any(token in key for token in ("KEY", "TOKEN", "SECRET", "CREDENTIAL"))
    }

    def redact(text):
        for value in secret_values:
            text = text.replace(value, "[REDACTED]")
        return text

    process_tree = ProcessTree()
    process = subprocess.Popen(
        args, cwd=cwd, env=actor_env,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        **process_tree.popen_kwargs(),
    )
    stdout_lines = []
    stderr_lines = []
    stdout_thread = None
    stderr_thread = None
    heartbeat_thread = None
    stop_heartbeat = threading.Event()

    def read_stream(stream, collector, label):
        try:
            for line in iter(stream.readline, ""):
                line = redact(line)
                collector.append(line)
                logger.info("[%s] %s", label, line.rstrip())
        finally:
            stream.close()

    def heartbeat_loop():
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL_SECONDS):
            state_store.heartbeat(job.job_id)

    timed_out = False
    try:
        # Popen is the ownership boundary. Every operation after it is inside
        # this cleanup region, including Job attachment and setup failures.
        process_tree.attach(process)
        stdout_thread = threading.Thread(
            target=read_stream, args=(process.stdout, stdout_lines, "AGENT"), daemon=True,
        )
        stderr_thread = threading.Thread(
            target=read_stream, args=(process.stderr, stderr_lines, "AGENT-ERR"), daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        state_store.mark_running(job.job_id, process.pid, HOSTNAME)
        if input_text is not None:
            process.stdin.write(input_text)
            process.stdin.close()
        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        # Descendants can inherit stdout/stderr. Settle the owned tree before
        # joining readers so inherited pipe handles cannot add output delay.
        try:
            try:
                process_tree.terminate(process)
            except Exception:
                # Attachment/Job API failure still leaves the direct child
                # owned by this invocation and safe to terminate by handle.
                if process.poll() is None:
                    process.kill()
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        finally:
            stop_heartbeat.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=2)
            if stdout_thread is not None:
                stdout_thread.join(timeout=5)
            if stderr_thread is not None:
                stderr_thread.join(timeout=5)
            process_tree.close()

    result = ProcessResult(
        returncode=process.returncode,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
    )
    if timed_out:
        raise ProcessTimeoutExpired(args, timeout, result)
    return result
