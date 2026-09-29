import json
import uuid

import state_store
from job_protocol import Job, job_from_row


def main():
    state_store.initialize()

    job_id = (
        "V3-STATE-TEST-"
        + uuid.uuid4().hex[:8]
    )

    instruction_ref = {
        "type": "notion_page",
        "page_id": (
            "3e930bde-8a72-806b-"
            "b974-e4419233781a"
        ),
    }

    job = Job(
        protocol_version="3",
        job_id=job_id,
        actor="claude",
        mode="review",
        workspace="local-agent",
        prompt="STATE-STORE-V3-TEST",
        prompt_sha256="a" * 64,
        instruction_ref=instruction_ref,
        callback_type=None,
        callback_url=None,
    )

    # -------------------------
    # INSERT
    # -------------------------

    state_store.create_job(job)

    row = state_store.get_job(
        job_id
    )

    assert row is not None
    assert row["protocol_version"] == "3"
    assert row["prompt"] == (
        "STATE-STORE-V3-TEST"
    )
    assert row["prompt_sha256"] == (
        "a" * 64
    )

    assert json.loads(
        row["instruction_ref"]
    ) == instruction_ref

    print(
        "PASS: create_job v3"
    )

    # -------------------------
    # QUEUE
    # -------------------------

    state_store.mark_queued(
        job_id
    )

    row = state_store.get_next_queued(
        "local-agent"
    )

    assert row is not None
    assert row["job_id"] == job_id

    print(
        "PASS: queued v3"
    )

    # -------------------------
    # DB row -> Job
    # -------------------------

    restored = job_from_row(
        row
    )

    assert restored.protocol_version == "3"
    assert restored.job_id == job_id

    assert restored.prompt == (
        "STATE-STORE-V3-TEST"
    )

    assert restored.prompt_sha256 == (
        "a" * 64
    )

    assert (
        restored.instruction_ref
        == instruction_ref
    )

    print(
        "PASS: job_from_row v3"
    )

    # -------------------------
    # Exact round-trip
    # -------------------------

    assert (
        restored.instruction_ref[
            "page_id"
        ]
        == instruction_ref["page_id"]
    )

    print(
        "PASS: instruction_ref round-trip"
    )

    # -------------------------
    # Cleanup
    # -------------------------

    with state_store.connect() as db:
        db.execute(
            "DELETE FROM jobs "
            "WHERE job_id = ?",
            (job_id,),
        )

    print(
        "PASS: cleanup"
    )

    print()
    print(
        "ALL STATE STORE v3 TESTS PASSED"
    )


if __name__ == "__main__":
    main()