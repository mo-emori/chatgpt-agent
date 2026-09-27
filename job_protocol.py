import json
import base64
from dataclasses import dataclass
from config import (
    ALLOWED_ACTOR_MODES,
    PROTOCOL_VERSION,
    WORKSPACES,
)
import hashlib
import re

SHA256_RE = re.compile(
    r"^[0-9a-f]{64}$"
)

class JobValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Job:
    protocol_version: str
    job_id: str
    actor: str
    mode: str
    workspace: str
    prompt: str
    prompt_sha256: str | None


def decode_and_verify_prompt(data):
    encoding = data.get(
        "prompt_encoding",
        "plain",
    )

    encoded_prompt = data["prompt"]

    if encoding == "base64":
        try:
            prompt_bytes = (
                base64.b64decode(
                    encoded_prompt,
                    validate=True,
                )
            )
        except Exception as e:
            raise JobValidationError(
                "INVALID_PROMPT_BASE64"
            ) from e

    elif encoding == "plain":
        prompt_bytes = (
            encoded_prompt.encode("utf-8")
        )

    else:
        raise JobValidationError(
            "UNSUPPORTED_PROMPT_ENCODING"
        )

    actual_hash = hashlib.sha256(
        prompt_bytes
    ).hexdigest()

    expected_hash = data.get(
        "prompt_sha256"
    )

    workspace = data.get(
        "workspace"
    )

    if workspace == "argus":
        if expected_hash is None:
            raise JobValidationError(
                "PROMPT_SHA256_REQUIRED"
            )

        if not SHA256_RE.fullmatch(
            expected_hash
        ):
            raise JobValidationError(
                "INVALID_PROMPT_SHA256_FORMAT"
            )

        if actual_hash != expected_hash:
            raise JobValidationError(
                "PROMPT_SHA256_MISMATCH"
            )

    elif expected_hash is not None:
        if not SHA256_RE.fullmatch(
            expected_hash
        ):
            raise JobValidationError(
                "INVALID_PROMPT_SHA256_FORMAT"
            )

        if actual_hash != expected_hash:
            raise JobValidationError(
                "PROMPT_SHA256_MISMATCH"
            )

    try:
        prompt = prompt_bytes.decode(
            "utf-8"
        )
    except UnicodeDecodeError as e:
        raise JobValidationError(
            "PROMPT_NOT_UTF8"
        ) from e

    return prompt, actual_hash


def parse_job(text: str) -> Job:
    text = text.strip()

    # ChatGPT / Slack がJOBをMarkdown code fenceで
    # 包む場合を許容する。
    #
    # 対応例:
    # ```json
    # {...}
    # ```
    #
    # ```{...}```
    #
    if text.startswith("```"):
        # opening fence
        text = text[3:]

        # optional language identifier
        if text.startswith("json"):
            text = text[4:]

        text = text.lstrip()

        # closing fence
        closing = text.find("```")

        if closing != -1:
            text = text[:closing]

        text = text.strip()

    decoder = json.JSONDecoder()

    try:
        data, _ = decoder.raw_decode(text)
    except json.JSONDecodeError as e:
        raise JobValidationError(
            f"Invalid JOB JSON: {e}"
        ) from e

    if not isinstance(data, dict):
        raise JobValidationError(
            "JOB must be a JSON object"
        )

    required = (
        "protocol_version",
        "job_id",
        "actor",
        "mode",
        "workspace",
        "prompt",
    )

    missing = [
        key
        for key in required
        if key not in data
    ]

    if missing:
        raise JobValidationError(
            f"Missing fields: {', '.join(missing)}"
        )

    if data["protocol_version"] != PROTOCOL_VERSION:
        raise JobValidationError(
            "UNKNOWN_PROTOCOL_VERSION"
        )

    actor_mode = (
        data["actor"],
        data["mode"],
    )

    if actor_mode not in ALLOWED_ACTOR_MODES:
        raise JobValidationError(
            f"Actor/mode not allowed: {actor_mode}"
        )

    if data["workspace"] not in WORKSPACES:
        raise JobValidationError(
            f"Unknown workspace: {data['workspace']}"
        )

    if not isinstance(data["job_id"], str) or not data["job_id"].strip():
        raise JobValidationError(
            "job_id must be non-empty"
        )

    if not isinstance(data["prompt"], str) or not data["prompt"].strip():
        raise JobValidationError(
            "prompt must be non-empty"
        )

    prompt, verified_hash = (
        decode_and_verify_prompt(data)
    )

    return Job(
        protocol_version=data["protocol_version"],
        job_id=data["job_id"],
        actor=data["actor"],
        mode=data["mode"],
        workspace=data["workspace"],
        prompt=prompt,
        prompt_sha256=verified_hash,
    )


def job_from_row(row):
    return Job(
        protocol_version=row["protocol_version"],
        job_id=row["job_id"],
        actor=row["actor"],
        mode=row["mode"],
        workspace=row["workspace"],
        prompt=row["prompt"],
        prompt_sha256=row["prompt_sha256"],
    )

