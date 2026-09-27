import subprocess

from config import (
    ACTOR_TIMEOUT,
    CODEX_CMD,
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

    prompt = append_manifest_instruction(
        job.prompt
    )

    args = [
        CODEX_CMD,
        "exec",
    ]

    if config["allow_skip_git_repo_check"]:
        args.append(
            "--skip-git-repo-check"
        )

    args += [
        "--sandbox",
        "workspace-write",
        "-",  # promptをstdinから読む
    ]

    return run_process(
        job=job,
        args=args,
        cwd=workdir,
        timeout=ACTOR_TIMEOUT["codex"],
        input_text=prompt,
    )