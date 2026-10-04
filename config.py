import os
import socket
import json
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

CODEX_SANDBOX_OVERRIDE = 'windows.sandbox="mxc"'

CLAUDE_CMD = os.environ["CLAUDE_CMD"]

EXPECTED_CLI = {
    "codex": "codex-cli 0.157.1",
    "claude": "2.1.280 (Claude Code)",
}

ALLOWED_ACTOR_MODES = {
    ("codex", "implementation"),
    ("claude", "review"),
}

ACTOR_TIMEOUT = {
    "codex": 7200,
    "claude": 7200,
}

CONTEXT_HARNESS_ACTIVATION_MODES = ("OFF", "SHADOW", "ENFORCE_AND_INJECT")
CONTEXT_HARNESS_ACTIVATION_MODE = os.environ.get(
    "CONTEXT_HARNESS_ACTIVATION_MODE", "OFF"
).strip().upper()
if CONTEXT_HARNESS_ACTIVATION_MODE not in CONTEXT_HARNESS_ACTIVATION_MODES:
    raise ValueError(
        "CONTEXT_HARNESS_ACTIVATION_MODE must be OFF, SHADOW, or ENFORCE_AND_INJECT"
    )

HEARTBEAT_INTERVAL_SECONDS = int(
    os.environ.get(
        "HEARTBEAT_INTERVAL_SECONDS",
        "60",
    )
)

HOSTNAME = socket.gethostname()

BASE_DIR = Path(__file__).parent

STATE_DB = BASE_DIR / "agent_state.db"

CONFIG_FILE = BASE_DIR / "config.json"

with CONFIG_FILE.open(
    encoding="utf-8"
) as f:
    FILE_CONFIG = json.load(f)

WORKSPACES = {}

for name, cfg in FILE_CONFIG[
    "workspaces"
].items():
    WORKSPACES[name] = {
        **cfg,
        "path": Path(
            cfg["path"]
        ).resolve(),
    }

BROWSER_CONFIG = FILE_CONFIG[
    "browser"
]
