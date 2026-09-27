import subprocess

from config import (
    CLAUDE_CMD,
    CODEX_CMD,
    EXPECTED_CLI,
)


class CliVersionError(RuntimeError):
    pass


def get_version(command):
    result = subprocess.run(
        [command, "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )

    if result.returncode != 0:
        raise CliVersionError(
            f"Version command failed: {command}"
        )

    return result.stdout.strip()


def check_cli_versions():
    actual = {
        "codex": get_version(CODEX_CMD),
        "claude": get_version(CLAUDE_CMD),
    }

    available = {}

    for actor, version in actual.items():
        expected = EXPECTED_CLI[actor]

        ok = version == expected
        available[actor] = ok

        print(
            f"{actor}: "
            f"{version} "
            f"{'OK' if ok else 'VERSION MISMATCH'}"
        )

    return available