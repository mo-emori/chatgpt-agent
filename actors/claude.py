from config import (
    ACTOR_TIMEOUT,
    CLAUDE_CMD,
    WORKSPACES,
)
from artifacts.manifest import (
    append_manifest_instruction,
)
from actors.process_runner import (
    run_process,
    validate_workspace,
)

def run(job, *, workdir=None, settings_path=None):
    config = WORKSPACES[job.workspace]
    validate_workspace(config)
    workdir = workdir or config["path"]
    if settings_path is None:
        raise ValueError("Claude review requires a managed settings_path")

    args = [
        CLAUDE_CMD,
        "-p",
        "--restricted",
        "--tools",
        "Read,Glob,Grep,Bash",
        "--settings",
        str(settings_path),
        "--safe-mode",
        "--strict-mcp-config",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
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
        actor="claude",
    )
