import os
import socket
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


PROTOCOL_VERSION = "1"

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

# PoCで確認済み
ALLOWED_CHANNEL_IDS = {
    "C0C4J6WMLJH",
}

# ChatGPT Slack PluginがJOB投稿時に使っていたID
ALLOWED_SENDER_IDS = {
    "U0C4J6R3221",
}

CODEX_CMD = os.environ.get(
    "CODEX_CMD",
    "codex.cmd",
)

CLAUDE_CMD = os.environ["CLAUDE_CMD"]

EXPECTED_CLI = {
    "codex": "codex-cli 0.157.1",
    "claude": "2.1.280 (Claude Code)",
}

WORKSPACES = {
    "sandbox": {
        "path": Path(
            r"C:\dev\chatgpt-agent\sandbox"
        ).resolve(),
        "git_required": False,
        "allow_skip_git_repo_check": True,
        "artifact_roots": [],
    },

    "argus": {
        "path": Path(
            r"C:\dev\argus"
        ).resolve(),
        "git_required": True,
        "allow_skip_git_repo_check": False,
        "artifact_roots": [
            "validation/reports",
            "validation/metrics",
            "tests",
        ],
    },
}

ALLOWED_ACTOR_MODES = {
    ("codex", "implementation"),
    ("claude", "review"),
}

ACTOR_TIMEOUT = {
    "codex": 7200,
    "claude": 7200,
}

HEARTBEAT_INTERVAL_SECONDS = int(
    os.environ.get(
        "HEARTBEAT_INTERVAL_SECONDS",
        "60",
    )
)

HOSTNAME = socket.gethostname()

STATE_DB = Path(
    r"C:\dev\chatgpt-agent\agent_state.db"
)