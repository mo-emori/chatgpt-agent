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
from urllib.parse import urlparse

SHA256_RE = re.compile(
    r"^[0-9a-f]{64}$"
)

SUPPORTED_PROTOCOL_VERSIONS = {
    "1",
    "2",
    "3",
}


class JobValidationError(ValueError):
    def __init__(self, message, *, callback_job=None):
        super().__init__(message)
        self.callback_job = callback_job


def validated_callback(data):
    callback = data.get("callback")
    if callback is None:
        return None, None
    if not isinstance(callback, dict):
        raise JobValidationError("INVALID_CALLBACK")
    callback_type = callback.get("type")
    callback_url = callback.get("url")
    if callback_type != "chatgpt_browser":
        raise JobValidationError("UNKNOWN_CALLBACK_TYPE")
    if not isinstance(callback_url, str):
        raise JobValidationError("INVALID_CALLBACK_URL")
    callback_url = callback_url.strip()
    if callback_url.startswith("<") and callback_url.endswith(">"):
        callback_url = callback_url[1:-1]
    if "|" in callback_url:
        callback_url = callback_url.split("|", 1)[0]
    callback_url = callback_url.strip()
    parsed = urlparse(callback_url)
    if parsed.scheme != "https" or parsed.hostname != "chatgpt.com":
        raise JobValidationError("INVALID_CALLBACK_URL")
    return callback_type, callback_url


@dataclass(frozen=True)
class Job:
    protocol_version: str
    job_id: str
    actor: str
    mode: str
    workspace: str
    prompt: str | None = None
    prompt_sha256: str | None = None
    instruction_ref: dict | None = None
    review_mode: str | None = None
    review_package_ref: dict | None = None
    measurement_mode: bool = False
    callback_type: str | None = None
    callback_url: str | None = None


def decode_and_verify_prompt(data, *, require_prompt_sha256):
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

    if require_prompt_sha256 and not expected_hash:
        raise JobValidationError(
            "PROMPT_SHA256_REQUIRED"
        )

    if expected_hash is not None:
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
            "utf-8",
            errors="strict",
        )
    except UnicodeDecodeError as e:
        raise JobValidationError(
            "PROMPT_NOT_UTF8"
        ) from e

    return prompt, actual_hash


def decode_prompt_v2(data):
    if (
        data.get("prompt_encoding")
        != "base64"
    ):
        raise JobValidationError(
            "INVALID_PROMPT_ENCODING"
        )

    encoded = data.get("prompt")

    if not isinstance(encoded, str):
        raise JobValidationError(
            "INVALID_PROMPT_BASE64"
        )

    try:
        prompt_bytes = base64.b64decode(
            encoded,
            validate=True,
        )
    except Exception as e:
        raise JobValidationError(
            "INVALID_PROMPT_BASE64"
        ) from e

    if not prompt_bytes:
        raise JobValidationError(
            "EMPTY_PROMPT"
        )

    return prompt_bytes


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

    base_required = (
        "protocol_version",
        "job_id",
        "actor",
        "mode",
        "workspace",
    )

    missing = [
        key
        for key in base_required
        if key not in data
    ]

    if missing:
        raise JobValidationError(
            f"Missing fields: {', '.join(missing)}"
        )

    protocol_version = data[
        "protocol_version"
    ]

    if not isinstance(protocol_version, str) or not protocol_version.strip():
        raise JobValidationError("UNKNOWN_PROTOCOL_VERSION")
    if not isinstance(data["job_id"], str) or not data["job_id"].strip():
        raise JobValidationError("job_id must be non-empty")
    for key in ("actor", "mode", "workspace"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise JobValidationError(f"{key} must be non-empty")

    # Once this succeeds, later semantic failures may safely wake exactly the
    # callback supplied by this authorized request without accepting the job.
    callback_type, callback_url = validated_callback(data)
    callback_job = Job(
        protocol_version=protocol_version,
        job_id=data["job_id"],
        actor=data["actor"],
        mode=data["mode"],
        workspace=data["workspace"],
        callback_type=callback_type,
        callback_url=callback_url,
    )

    def reject(message):
        raise JobValidationError(message, callback_job=callback_job)

    if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        reject("UNKNOWN_PROTOCOL_VERSION")

    if protocol_version in {"1", "2"} and "prompt" not in data:
        reject("Missing fields: prompt")
    if protocol_version == "3" and "instruction_ref" not in data:
        reject("Missing fields: instruction_ref")

    actor_mode = (
        data["actor"],
        data["mode"],
    )

    if actor_mode not in ALLOWED_ACTOR_MODES:
        reject(f"Actor/mode not allowed: {actor_mode}")

    if data["workspace"] not in WORKSPACES:
        reject(f"Unknown workspace: {data['workspace']}")

    if protocol_version in {"1", "2"}:
        if (
            not isinstance(
                data["prompt"],
                str,
            )
            or not data["prompt"].strip()
        ):
            reject("prompt must be non-empty")

    review_mode = None
    review_package_ref = None
    measurement_mode = False

    if protocol_version == "1":
        # 現行処理をそのまま維持
        try:
            prompt, verified_hash = decode_and_verify_prompt(
                data,
                require_prompt_sha256=WORKSPACES[data["workspace"]][
                    "protocol_v1_require_prompt_sha256"
                ],
            )
        except JobValidationError as e:
            reject(str(e))

        instruction_ref = None

    elif protocol_version == "2":
        if "prompt_sha256" in data:
            reject("PROMPT_SHA256_NOT_ALLOWED")

        try:
            prompt_bytes = decode_prompt_v2(data)
        except JobValidationError as e:
            reject(str(e))

        verified_hash = hashlib.sha256(
            prompt_bytes
        ).hexdigest()

        try:
            prompt = prompt_bytes.decode(
                "utf-8",
                errors="strict",
            )
        except UnicodeDecodeError as e:
            reject("PROMPT_NOT_UTF8")

        instruction_ref = None

    elif protocol_version == "3":
        if "prompt" in data:
            reject("PROMPT_NOT_ALLOWED")

        if "prompt_encoding" in data:
            reject("PROMPT_ENCODING_NOT_ALLOWED")

        if "prompt_sha256" in data:
            reject("PROMPT_SHA256_NOT_ALLOWED")

        ref = data.get(
            "instruction_ref"
        )

        if not isinstance(ref, dict):
            reject("INSTRUCTION_REF_INVALID")

        if ref.get("type") != "notion_page":
            reject("INSTRUCTION_REF_INVALID")

        page_id = ref.get(
            "page_id"
        )

        if (
            not isinstance(page_id, str)
            or not page_id.strip()
        ):
            reject("INSTRUCTION_REF_INVALID")
        
        prompt = None
        verified_hash = None
        instruction_ref = ref

        review_mode = data.get("review_mode")
        if review_mode is not None and review_mode not in (
            "DELTA_REVIEW", "BOUNDARY_REVIEW", "FULL_REVIEW"
        ):
            reject("INVALID_REVIEW_MODE")
        review_package_ref = data.get("review_package_ref")
        if review_package_ref is not None:
            if not isinstance(review_package_ref, dict) or set(review_package_ref) - {
                "path", "sha256", "target_job_id"
            }:
                reject("REVIEW_PACKAGE_REF_INVALID")
            if not isinstance(review_package_ref.get("path"), str) or not review_package_ref["path"]:
                reject("REVIEW_PACKAGE_REF_INVALID")
            if not SHA256_RE.fullmatch(str(review_package_ref.get("sha256", ""))):
                reject("REVIEW_PACKAGE_REF_INVALID")
            if not isinstance(review_package_ref.get("target_job_id"), str) or not review_package_ref["target_job_id"]:
                reject("REVIEW_PACKAGE_REF_INVALID")
        supplied_measurement_mode = data.get("measurement_mode")
        if supplied_measurement_mode is not None and not isinstance(supplied_measurement_mode, bool):
            reject("MEASUREMENT_MODE_INVALID")
        if (review_mode is not None or review_package_ref is not None or supplied_measurement_mode is not None) and actor_mode != ("claude", "review"):
            reject("REVIEW_METADATA_NOT_ALLOWED")
        if review_mode == "DELTA_REVIEW" and review_package_ref is None:
            reject("DELTA_REVIEW_REQUIRES_MEASUREMENT_PACKAGE")
        if review_mode == "DELTA_REVIEW" and supplied_measurement_mode is False:
            reject("DELTA_REVIEW_REQUIRES_MEASUREMENT_PACKAGE")
        if review_mode == "BOUNDARY_REVIEW" and review_package_ref is not None:
            reject("BOUNDARY_PACKAGE_NOT_SUPPORTED")
        if review_mode != "DELTA_REVIEW" and supplied_measurement_mode:
            reject("MEASUREMENT_MODE_REQUIRES_DELTA_REVIEW")
        # A validated DELTA_REVIEW package reference is the wire-level
        # measurement activation contract.  Keep accepting the former explicit
        # true flag, but normalize both shapes to the same internal state.
        measurement_mode = review_mode == "DELTA_REVIEW" and review_package_ref is not None

    # callback
    callback = data.get(
        "callback"
    )

    callback_type = None
    callback_url = None

    if callback is not None:
        if not isinstance(
            callback,
            dict,
        ):
            raise JobValidationError(
                "INVALID_CALLBACK"
            )

        callback_type = (
            callback.get("type")
        )
        
        callback_url = callback.get(
            "url"
        )

        # -------------------------
        # Callback type validation
        # -------------------------

        if (
            callback_type
            != "chatgpt_browser"
        ):
            raise JobValidationError(
                "UNKNOWN_CALLBACK_TYPE"
            )

        # -------------------------
        # Callback URL validation
        # -------------------------

        if not isinstance(
            callback_url,
            str,
        ):
            raise JobValidationError(
                "INVALID_CALLBACK_URL"
            )

        callback_url = (
            callback_url.strip()
        )

        # Slack mrkdwn normalization
        #
        # https://chatgpt.com/c/...
        #
        # ↓ Slackで以下になる場合がある
        #
        # <https://chatgpt.com/c/...>
        #
        # または
        #
        # <https://chatgpt.com/c/...|label>

        if (
            callback_url.startswith("<")
            and callback_url.endswith(">")
        ):
            callback_url = (
                callback_url[1:-1]
            )

        if "|" in callback_url:
            callback_url = (
                callback_url.split(
                    "|",
                    1,
                )[0]
            )

        callback_url = (
            callback_url.strip()
        )

        # -------------------------
        # Allowed callback origin
        # -------------------------

        parsed = urlparse(
            callback_url
        )

        if (
            parsed.scheme != "https"
            or parsed.hostname
            != "chatgpt.com"
        ):
            raise JobValidationError(
                "INVALID_CALLBACK_URL"
            )

    return Job(
        protocol_version=data["protocol_version"],
        job_id=data["job_id"],
        actor=data["actor"],
        mode=data["mode"],
        workspace=data["workspace"],
        prompt=prompt,
        prompt_sha256=verified_hash,
        instruction_ref=instruction_ref,
        review_mode=review_mode,
        review_package_ref=review_package_ref,
        measurement_mode=measurement_mode,
        callback_type=callback_type,
        callback_url=callback_url,
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
        instruction_ref=(
            json.loads(row["instruction_ref"])
            if row["instruction_ref"]
            else None
        ),
        review_mode=(row["review_mode"] if "review_mode" in row.keys() else None),
        review_package_ref=(json.loads(row["review_package_ref"])
                            if "review_package_ref" in row.keys() and row["review_package_ref"] else None),
        measurement_mode=(bool(row["measurement_mode"])
                          if "measurement_mode" in row.keys() else False),
        callback_type=row["callback_type"],
        callback_url=row["callback_url"],
    )

