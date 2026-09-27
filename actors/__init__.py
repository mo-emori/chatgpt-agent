from actors import claude, codex


def run_agent(job):
    if job.actor == "codex":
        return codex.run(job)

    if job.actor == "claude":
        return claude.run(job)

    raise ValueError(
        f"Unsupported actor: {job.actor}"
    )