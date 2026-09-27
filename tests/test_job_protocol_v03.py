import base64
import hashlib
import json

from job_protocol import (
    JobValidationError,
    parse_job,
)


def encode_prompt(prompt: str):
    raw = prompt.encode("utf-8")

    return (
        base64.b64encode(raw).decode("ascii"),
        hashlib.sha256(raw).hexdigest(),
    )


def make_job(
    *,
    job_id,
    workspace,
    prompt,
    prompt_sha256=True,
):
    encoded, sha256 = encode_prompt(prompt)

    data = {
        "protocol_version": "1",
        "job_id": job_id,
        "actor": "claude",
        "mode": "review",
        "workspace": workspace,
        "prompt_encoding": "base64",
        "prompt": encoded,
    }

    if prompt_sha256 is True:
        data["prompt_sha256"] = sha256
    elif isinstance(prompt_sha256, str):
        data["prompt_sha256"] = prompt_sha256

    return json.dumps(
        data,
        ensure_ascii=False,
    ), sha256


def expect_success(name, text, expected_hash):
    job = parse_job(text)

    assert job.prompt_sha256 == expected_hash

    print(
        f"PASS: {name}"
        f" hash={job.prompt_sha256}"
    )


def expect_failure(
    name,
    text,
    expected_error,
):
    try:
        parse_job(text)

    except JobValidationError as e:
        actual = str(e)

        assert actual == expected_error, (
            f"{name}: "
            f"expected={expected_error!r}, "
            f"actual={actual!r}"
        )

        print(
            f"PASS: {name}"
            f" -> {actual}"
        )
        return

    raise AssertionError(
        f"{name}: expected failure"
    )


# ---------------------------------------
# 1. Valid: ARGUS + correct SHA
# ---------------------------------------

text, sha = make_job(
    job_id="PROTO-V03-VALID-001",
    workspace="argus",
    prompt=(
        '日本語 Prompt\n'
        'JSON: {"a":"b"}\n'
        'Quote: "hello"\n'
        'multiline test'
    ),
)

expect_success(
    "valid argus SHA",
    text,
    sha,
)


# ---------------------------------------
# 2. Invalid: wrong SHA
# ---------------------------------------

text, _ = make_job(
    job_id="PROTO-V03-BAD-HASH-001",
    workspace="argus",
    prompt="hash mismatch test",
    prompt_sha256="0" * 64,
)

expect_failure(
    "wrong SHA",
    text,
    "PROMPT_SHA256_MISMATCH",
)


# ---------------------------------------
# 3. Invalid: ARGUS hash missing
# ---------------------------------------

text, _ = make_job(
    job_id="PROTO-V03-NO-HASH-001",
    workspace="argus",
    prompt="missing hash test",
    prompt_sha256=False,
)

expect_failure(
    "missing ARGUS SHA",
    text,
    "PROMPT_SHA256_REQUIRED",
)


# ---------------------------------------
# 4. Invalid: malformed SHA
# ---------------------------------------

text, _ = make_job(
    job_id="PROTO-V03-BAD-FORMAT-001",
    workspace="argus",
    prompt="bad hash format",
    prompt_sha256="abc",
)

expect_failure(
    "invalid SHA format",
    text,
    "INVALID_PROMPT_SHA256_FORMAT",
)


# ---------------------------------------
# 5. Invalid: modified Base64 Prompt
# ---------------------------------------

text, sha = make_job(
    job_id="PROTO-V03-TAMPER-001",
    workspace="argus",
    prompt="original prompt",
)

data = json.loads(text)

tampered = "modified prompt"

data["prompt"] = base64.b64encode(
    tampered.encode("utf-8")
).decode("ascii")

text = json.dumps(data)

expect_failure(
    "tampered Prompt",
    text,
    "PROMPT_SHA256_MISMATCH",
)


print()
print("ALL JOB PROTOCOL v0.3 TESTS PASSED")