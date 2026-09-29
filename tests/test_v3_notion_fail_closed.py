import json
from unittest.mock import patch

import agent_worker
from notion_client import NotionInstructionError


FAILURES = [
    "INSTRUCTION_NOT_FOUND",
    "INSTRUCTION_PERMISSION_DENIED",
    "INSTRUCTION_BLOCK_MISSING",
    "INSTRUCTION_BLOCK_AMBIGUOUS",
    "INSTRUCTION_EMPTY",
    "NOTION_UNAVAILABLE",
]


def make_job(job_id):
    return json.dumps(
        {
            "protocol_version": "3",
            "job_id": job_id,
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
    )


def run_case(failure_class):
    job_id = (
        "V3-FAIL-CLOSED-"
        + failure_class
    )

    sent = []

    def say(message):
        sent.append(message)

    with (
        patch.object(
            agent_worker.state_store,
            "get_job",
            return_value=None,
        ),
        patch.object(
            agent_worker,
            "resolve_v3_instruction",
            side_effect=NotionInstructionError(
                failure_class
            ),
        ) as resolve_mock,
        patch.object(
            agent_worker.state_store,
            "create_job",
        ) as create_mock,
        patch.object(
            agent_worker.threading,
            "Thread",
        ) as thread_mock,
    ):
        agent_worker.process_message(
            text=make_job(job_id),
            channel="TEST",
            sender="TEST",
            say=say,
        )

    # Notion resolution attempted exactly once.
    resolve_mock.assert_called_once()

    # Fail Closed:
    # no DB creation and no Actor thread.
    create_mock.assert_not_called()
    thread_mock.assert_not_called()

    assert len(sent) == 1

    payload_text = sent[0]

    assert '"status": "BRIDGE_ERROR"' in (
        payload_text
    )

    assert (
        f'"failure_class": '
        f'"{failure_class}"'
        in payload_text
    )

    assert (
        f'"error_summary": '
        f'"{failure_class}"'
        in payload_text
    )


def main():
    failed = 0

    for failure_class in FAILURES:
        try:
            run_case(
                failure_class
            )

            print(
                "PASS:",
                failure_class,
            )

        except Exception as e:
            failed += 1

            print(
                "FAIL:",
                failure_class,
                type(e).__name__,
                e,
            )

    if failed:
        raise SystemExit(
            f"{failed} test(s) failed"
        )

    print()
    print(
        "ALL v3 NOTION FAIL-CLOSED "
        "TESTS PASSED"
    )


if __name__ == "__main__":
    main()