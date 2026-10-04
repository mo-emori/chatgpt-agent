from actors import claude, codex


def run_agent(job, **kwargs):
    if job.actor == "codex":
        return codex.run(job, **kwargs)

    if job.actor == "claude":
        return claude.run(job)

    raise ValueError(
        f"Unsupported actor: {job.actor}"
    )
