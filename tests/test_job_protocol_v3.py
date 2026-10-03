import json

from job_protocol import (
    JobValidationError,
    parse_job,
)


def make_v3_job(**overrides):
    data = {
        "protocol_version": "3",
        "job_id": "V3-TEST-001",
        "actor": "claude",
        "mode": "review",
        "workspace": "local-agent",
        "instruction_ref": {
            "type": "notion_page",
            "page_id": (
                "3e930bde-8a72-806b-"
                "b974-e4419233781a"
            ),
        },
    }

    data.update(overrides)
    return data


def parse(data):
    return parse_job(
        json.dumps(
            data,
            ensure_ascii=False,
        )
    )


def expect_error(
    data,
    expected,
):
    try:
        parse(data)
    except JobValidationError as e:
        assert str(e) == expected
        return

    raise AssertionError(
        "JobValidationError was not raised"
    )


def test_v3_valid():
    job = parse(
        make_v3_job()
    )

    assert job.protocol_version == "3"
    assert job.prompt is None
    assert job.prompt_sha256 is None

    assert job.instruction_ref == {
        "type": "notion_page",
        "page_id": (
            "3e930bde-8a72-806b-"
            "b974-e4419233781a"
        ),
    }


def test_v3_missing_instruction_ref():
    data = make_v3_job()
    del data["instruction_ref"]

    expect_error(
        data,
        "Missing fields: instruction_ref",
    )


def test_v3_invalid_instruction_ref_type():
    data = make_v3_job(
        instruction_ref="not-a-dict"
    )

    expect_error(
        data,
        "INSTRUCTION_REF_INVALID",
    )


def test_v3_invalid_instruction_type():
    data = make_v3_job(
        instruction_ref={
            "type": "other",
            "page_id": "abc",
        }
    )

    expect_error(
        data,
        "INSTRUCTION_REF_INVALID",
    )


def test_v3_missing_page_id():
    data = make_v3_job(
        instruction_ref={
            "type": "notion_page",
        }
    )

    expect_error(
        data,
        "INSTRUCTION_REF_INVALID",
    )


def test_v3_empty_page_id():
    data = make_v3_job(
        instruction_ref={
            "type": "notion_page",
            "page_id": "   ",
        }
    )

    expect_error(
        data,
        "INSTRUCTION_REF_INVALID",
    )


def test_v3_rejects_prompt():
    data = make_v3_job(
        prompt="must not exist"
    )

    expect_error(
        data,
        "PROMPT_NOT_ALLOWED",
    )


def test_v3_rejects_prompt_encoding():
    data = make_v3_job(
        prompt_encoding="base64"
    )

    expect_error(
        data,
        "PROMPT_ENCODING_NOT_ALLOWED",
    )


def test_v3_rejects_prompt_sha256():
    data = make_v3_job(
        prompt_sha256="0" * 64
    )

    expect_error(
        data,
        "PROMPT_SHA256_NOT_ALLOWED",
    )


def test_v3_delta_review_metadata_is_explicit_and_validated():
    ref = {"path": "validation/context/C/J/review-package",
           "sha256": "a" * 64, "target_job_id": "IMPLEMENT-1"}
    job = parse(make_v3_job(review_mode="DELTA_REVIEW",
                            review_package_ref=ref, measurement_mode=True))
    assert job.review_mode == "DELTA_REVIEW"
    assert job.review_package_ref == ref
    assert job.measurement_mode is True


def test_v3_delta_review_rejects_silent_fallback_shapes():
    expect_error(make_v3_job(review_mode="DELTA_REVIEW"),
                 "DELTA_REVIEW_REQUIRES_MEASUREMENT_PACKAGE")
    expect_error(make_v3_job(review_mode="FULL_REVIEW", measurement_mode=True),
                 "MEASUREMENT_MODE_REQUIRES_DELTA_REVIEW")
    expect_error(make_v3_job(review_mode="BOUNDARY_REVIEW",
        review_package_ref={"path": "x", "sha256": "a" * 64, "target_job_id": "J"}),
        "BOUNDARY_PACKAGE_NOT_SUPPORTED")


TESTS = [
    test_v3_valid,
    test_v3_missing_instruction_ref,
    test_v3_invalid_instruction_ref_type,
    test_v3_invalid_instruction_type,
    test_v3_missing_page_id,
    test_v3_empty_page_id,
    test_v3_rejects_prompt,
    test_v3_rejects_prompt_encoding,
    test_v3_rejects_prompt_sha256,
    test_v3_delta_review_metadata_is_explicit_and_validated,
    test_v3_delta_review_rejects_silent_fallback_shapes,
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
        "ALL JOB PROTOCOL v3 TESTS PASSED"
    )
