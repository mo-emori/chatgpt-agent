import os


# Deliberately small, actor-specific inheritance sets.  Worker integration
# credentials (Slack, Notion, Google/Drive) are intentionally absent.
COMMON_ENV = {
    "ALLUSERSPROFILE", "APPDATA", "COMSPEC", "HOMEDRIVE", "HOMEPATH",
    "LOCALAPPDATA", "NUMBER_OF_PROCESSORS", "OS", "PATH", "PATHEXT",
    "PROCESSOR_ARCHITECTURE", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMW6432", "PROMPT", "PSMODULEPATH",
    "PUBLIC", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERDOMAIN",
    "USERNAME", "USERPROFILE", "WINDIR",
}

CODEX_AUTH_ENV = {
    "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "OPENAI_BASE_URL",
    "CODEX_HOME", "CODEX_API_KEY",
}

CLAUDE_AUTH_ENV = {
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION",
    "CLOUD_ML_REGION", "VERTEX_REGION_CLAUDE",
    "AZURE_API_KEY", "AZURE_RESOURCE_NAME",
}


def build_actor_env(actor, source=None):
    source = os.environ if source is None else source
    allowed = COMMON_ENV | (CODEX_AUTH_ENV if actor == "codex" else CLAUDE_AUTH_ENV)
    env = {key: source[key] for key in allowed if key in source}
    if actor == "claude":
        env.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
            "GIT_OPTIONAL_LOCKS": "0",
            "CLAUDE_CODE_SAFE_MODE": "1",
        })
    return env
