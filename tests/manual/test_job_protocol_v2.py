import base64
import hashlib

from job_protocol import (
    JobValidationError,
    parse_job,
)


def make_base_job(
    *,
    protocol_version,
    prompt_bytes,
    prompt_sha256=None,
    workspace="local-agent",
):
    data = {
        "protocol_version": protocol_version,
        "job_id": "TEST-V2-001",
        "actor": "claude",
        "mode": "review",
        "workspace": workspace,
        "prompt_encoding": "base64",
        "prompt": base64.b64encode(
            prompt_bytes
        ).decode("ascii"),
    }

    if prompt_sha256 is not None:
        data["prompt_sha256"] = (
            prompt_sha256
        )

    return data


def to_json(data):
    import json
    return json.dumps(
        data,
        ensure_ascii=False,
    )


def expect_error(
    data,
    expected_message,
):
    try:
        parse_job(to_json(data))
    except JobValidationError as e:
        assert str(e) == expected_message
        return

    raise AssertionError(
        "JobValidationError was not raised"
    )


# --------------------------------------------------
# v2 valid
# --------------------------------------------------

def test_v2_valid_without_request_sha():
    original_bytes = (
        "Protocol v2 test\n"
        "日本語テスト\n"
        'JSON: {"a":"b"}\n'
        r"Path: C:\dev\argus"
    ).encode("utf-8")

    data = make_base_job(
        protocol_version="2",
        prompt_bytes=original_bytes,
    )

    job = parse_job(
        to_json(data)
    )

    expected_sha = hashlib.sha256(
        original_bytes
    ).hexdigest()

    assert job.protocol_version == "2"

    assert (
        job.prompt_sha256
        == expected_sha
    )

    assert (
        job.prompt.encode("utf-8")
        == original_bytes
    )


# --------------------------------------------------
# Central invariant:
#
# decoded_prompt_bytes
# ==
# bytes hashed
# ==
# bytes reconstructed for Actor stdin
# --------------------------------------------------

def test_v2_byte_identity():
    original_bytes = (
        b'line1\n'
        b'JSON: {"x":"y"}\n'
        b'Path: C:\\dev\\argus\n'
        b'URL: https://example.com/a?x=1&y=2\n'
        b'Markdown: *bold* _italic_ `code`\n'
    )

    data = make_base_job(
        protocol_version="2",
        prompt_bytes=original_bytes,
    )

    job = parse_job(
        to_json(data)
    )

    decoded_bytes = base64.b64decode(
        data["prompt"],
        validate=True,
    )

    actor_stdin_bytes = (
        job.prompt.encode("utf-8")
    )

    assert decoded_bytes == original_bytes

    assert (
        actor_stdin_bytes
        == original_bytes
    )

    assert (
        job.prompt_sha256
        == hashlib.sha256(
            original_bytes
        ).hexdigest()
    )


# --------------------------------------------------
# Japanese / multiline / special chars
# --------------------------------------------------

def test_v2_utf8_round_trip():
    text = (
        "日本語Prompt\n"
        "\n"
        'Quote: "hello"\n'
        "SingleQuote: 'hello'\n"
        r"Backslash: C:\dev\argus"
        "\n"
        'JSON: {"a":"b"}\n'
        "URL: https://chatgpt.com/c/test\n"
        "Angle: <hello>\n"
        "Ampersand: a&b\n"
        "Markdown: *hello* _world_\n"
        "Backtick: `hello`\n"
    )

    original_bytes = text.encode(
        "utf-8"
    )

    data = make_base_job(
        protocol_version="2",
        prompt_bytes=original_bytes,
    )

    job = parse_job(
        to_json(data)
    )

    assert job.prompt == text

    assert (
        job.prompt.encode("utf-8")
        == original_bytes
    )


# --------------------------------------------------
# v2 request SHA is prohibited
# --------------------------------------------------

def test_v2_rejects_request_sha():
    prompt_bytes = b"hello v2"

    request_sha = hashlib.sha256(
        prompt_bytes
    ).hexdigest()

    data = make_base_job(
        protocol_version="2",
        prompt_bytes=prompt_bytes,
        prompt_sha256=request_sha,
    )

    expect_error(
        data,
        "PROMPT_SHA256_NOT_ALLOWED",
    )


# --------------------------------------------------
# Invalid Base64
# --------------------------------------------------

def test_v2_invalid_base64():
    data = {
        "protocol_version": "2",
        "job_id": "TEST-V2-BAD-B64",
        "actor": "claude",
        "mode": "review",
        "workspace": "local-agent",
        "prompt_encoding": "base64",
        "prompt": "%%%INVALID%%%",
    }

    expect_error(
        data,
        "INVALID_PROMPT_BASE64",
    )


# --------------------------------------------------
# Empty decoded Prompt
# --------------------------------------------------

def test_v2_empty_prompt():
    data = {
        "protocol_version": "2",
        "job_id": "TEST-V2-EMPTY",
        "actor": "claude",
        "mode": "review",
        "workspace": "local-agent",
        "prompt_encoding": "base64",
        "prompt": "",
    }

    # parse_job() currently has an earlier
    # non-empty prompt-field validation.
    #
    # To test decoded-empty specifically,
    # use a Base64 representation that decodes
    # to zero bytes if the parser permits it.
    #
    # Current parser may reject this earlier as:
    # "prompt must be non-empty".
    try:
        parse_job(to_json(data))
    except JobValidationError as e:
        assert str(e) in {
            "prompt must be non-empty",
            "EMPTY_PROMPT",
        }
        return

    raise AssertionError(
        "Empty Prompt was accepted"
    )


# --------------------------------------------------
# Invalid UTF-8
# --------------------------------------------------

def test_v2_invalid_utf8():
    original_bytes = bytes([
        0xFF,
        0xFE,
        0xFD,
    ])

    data = make_base_job(
        protocol_version="2",
        prompt_bytes=original_bytes,
    )

    expect_error(
        data,
        "PROMPT_NOT_UTF8",
    )


# --------------------------------------------------
# Unknown protocol
# --------------------------------------------------

def test_unknown_protocol():
    data = make_base_job(
        protocol_version="999",
        prompt_bytes=b"hello",
    )

    expect_error(
        data,
        "UNKNOWN_PROTOCOL_VERSION",
    )


# --------------------------------------------------
# v1 compatibility
# --------------------------------------------------

def test_v1_valid_argus():
    prompt_bytes = (
        "v1 compatibility test"
    ).encode("utf-8")

    sha = hashlib.sha256(
        prompt_bytes
    ).hexdigest()

    data = make_base_job(
        protocol_version="1",
        prompt_bytes=prompt_bytes,
        prompt_sha256=sha,
        workspace="argus",
    )

    job = parse_job(
        to_json(data)
    )

    assert job.protocol_version == "1"
    assert job.prompt_sha256 == sha

    assert (
        job.prompt.encode("utf-8")
        == prompt_bytes
    )


def test_v1_missing_sha_argus():
    data = make_base_job(
        protocol_version="1",
        prompt_bytes=b"hello",
        workspace="argus",
    )

    expect_error(
        data,
        "PROMPT_SHA256_REQUIRED",
    )


def test_v1_sha_mismatch_argus():
    data = make_base_job(
        protocol_version="1",
        prompt_bytes=b"Prompt A",
        prompt_sha256=hashlib.sha256(
            b"Prompt B"
        ).hexdigest(),
        workspace="argus",
    )

    expect_error(
        data,
        "PROMPT_SHA256_MISMATCH",
    )


# --------------------------------------------------
# Simple runner
# --------------------------------------------------

TESTS = [
    test_v2_valid_without_request_sha,
    test_v2_byte_identity,
    test_v2_utf8_round_trip,
    test_v2_rejects_request_sha,
    test_v2_invalid_base64,
    test_v2_empty_prompt,
    test_v2_invalid_utf8,
    test_unknown_protocol,
    test_v1_valid_argus,
    test_v1_missing_sha_argus,
    test_v1_sha_mismatch_argus,
]


if __name__ == "__main__":
    failed = 0

    for test in TESTS:
        try:
            test()
            print(
                f"PASS: {test.__name__}"
            )
        except Exception as e:
            failed += 1
            print(
                f"FAIL: {test.__name__}: "
                f"{type(e).__name__}: {e}"
            )

    if failed:
        raise SystemExit(
            f"{failed} test(s) failed"
        )

    print()
    print(
        "ALL JOB PROTOCOL v2 TESTS PASSED"
    )