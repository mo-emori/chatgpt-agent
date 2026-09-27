import subprocess

from config import (
    ACTOR_TIMEOUT,
    CLAUDE_CMD,
    WORKSPACES,
)
from actors.process_runner import run_process
from artifacts.manifest import (
    append_manifest_instruction,
)
from actors.process_runner import (
    run_process,
    validate_workspace,
)

def run(job):
    config = WORKSPACES[job.workspace]
    validate_workspace(config)
    workdir = config["path"]

    args = [
        CLAUDE_CMD,
        "-p",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--allowedTools",
        "Read,Glob,Grep",
    ]

    prompt = append_manifest_instruction(
        job.prompt
    )

    return run_process(
        job=job,
        args=args,
        cwd=workdir,
        timeout=ACTOR_TIMEOUT["claude"],
        input_text=prompt,
    )