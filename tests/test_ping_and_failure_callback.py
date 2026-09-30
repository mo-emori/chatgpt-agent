import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_worker
import slack_bridge
from notion_client import NotionInstructionError


CALLBACK_URL = "https://chatgpt.com/c/callback-test"


def callback_job(**overrides):
    values = {
        "protocol_version": "1",
        "job_id": "JOB-CALLBACK-1",
        "actor": "claude",
        "mode": "review",
        "workspace": "argus",
        "prompt_sha256": "a" * 64,
        "instruction_ref": None,
        "callback_type": "chatgpt_browser",
        "callback_url": CALLBACK_URL,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class PingTests(unittest.TestCase):
    def make_bridge(self):
        bridge = slack_bridge.SlackBridge.__new__(slack_bridge.SlackBridge)
        bridge.message_handler = Mock()
        return bridge

    def test_authorized_exact_ping_gets_one_plaintext_pong_and_no_job(self):
        bridge = self.make_bridge()
        say = Mock()
        event = {
            "channel": next(iter(slack_bridge.ALLOWED_CHANNEL_IDS)),
            "user": next(iter(slack_bridge.ALLOWED_SENDER_IDS)),
            "text": "LOCAL-AGENT PING",
        }

        bridge._handle_message(event, say)

        say.assert_called_once_with("LOCAL-AGENT PONG — Worker ready")
        bridge.message_handler.assert_not_called()

    def test_ordinary_non_job_text_remains_ignored_by_worker(self):
        say = Mock()
        with patch.object(agent_worker, "parse_job") as parse_job:
            agent_worker.process_message(
                text="hello worker", channel="C", sender="U", say=say
            )
        parse_job.assert_not_called()
        say.assert_not_called()

    def test_slack_attributed_ping_gets_one_plaintext_pong_and_no_job(self):
        bridge = self.make_bridge()
        say = Mock()
        event = {
            "channel": next(iter(slack_bridge.ALLOWED_CHANNEL_IDS)),
            "user": next(iter(slack_bridge.ALLOWED_SENDER_IDS)),
            "text": (
                "LOCAL-AGENT PING *使用して送信されました* "
                "<@U0C4T02QR9A>"
            ),
        }

        bridge._handle_message(event, say)

        say.assert_called_once_with("LOCAL-AGENT PONG — Worker ready")
        bridge.message_handler.assert_not_called()

    def test_arbitrary_ping_lookalike_is_not_treated_as_ping(self):
        bridge = self.make_bridge()
        say = Mock()
        event = {
            "channel": next(iter(slack_bridge.ALLOWED_CHANNEL_IDS)),
            "user": next(iter(slack_bridge.ALLOWED_SENDER_IDS)),
            "text": "LOCAL-AGENT PING SOMETHING",
        }

        bridge._handle_message(event, say)

        say.assert_not_called()
        bridge.message_handler.assert_called_once()

    def test_unauthorized_ping_gets_no_pong(self):
        bridge = self.make_bridge()
        say = Mock()
        bridge._handle_message(
            {"channel": "UNAUTHORIZED", "user": "UNAUTHORIZED", "text": "LOCAL-AGENT PING"},
            say,
        )
        say.assert_not_called()
        bridge.message_handler.assert_not_called()


class FailureCallbackTests(unittest.TestCase):
    def test_notion_resolve_failure_publishes_slack_before_callback(self):
        payload = json.dumps({
            "protocol_version": "3",
            "job_id": "V3-NOTION-FAIL",
            "actor": "claude",
            "mode": "review",
            "workspace": "local-agent",
            "instruction_ref": {"type": "notion_page", "page_id": "page-1"},
            "callback": {"type": "chatgpt_browser", "url": CALLBACK_URL},
        })
        events = []

        with (
            patch.object(agent_worker.state_store, "get_job", return_value=None),
            patch.object(
                agent_worker,
                "resolve_v3_instruction",
                side_effect=NotionInstructionError("NOTION_UNAVAILABLE"),
            ),
            patch.object(
                agent_worker,
                "send_browser_callback",
                side_effect=lambda *a, **k: events.append(("callback", k)),
            ),
            patch.object(agent_worker.state_store, "create_job") as create_job,
        ):
            agent_worker.process_message(
                text=payload,
                channel="C",
                sender="U",
                say=lambda message: events.append(("slack", message)),
            )

        self.assertEqual([event[0] for event in events], ["slack", "callback"])
        self.assertEqual(events[1][1]["failure_class"], "NOTION_UNAVAILABLE")
        create_job.assert_not_called()

    def test_failed_and_success_callback_markers_and_safe_body(self):
        job = callback_job()
        with patch.object(agent_worker, "notify_chatgpt") as notify:
            agent_worker.send_browser_callback(
                job,
                status="FAILED",
                artifact_status="NOT_RUN",
                failure_class="ACTOR_FAILED",
            )
            failed = notify.call_args.kwargs["message"]
            agent_worker.send_browser_callback(
                job, status="DONE", artifact_status="DONE"
            )
            succeeded = notify.call_args.kwargs["message"]

        self.assertTrue(failed.startswith("LOCAL_AGENT_JOB_FAILED\n"))
        self.assertIn("failure_class: ACTOR_FAILED", failed)
        self.assertIn("continue Failure Closure", failed)
        self.assertNotIn("stderr", failed)
        self.assertNotIn("prompt", failed.lower())
        self.assertTrue(succeeded.startswith("LOCAL_AGENT_JOB_COMPLETED\n"))

    def test_timeout_result_precedes_failed_callback(self):
        events = []
        job = callback_job()
        with (
            patch.object(agent_worker.state_store, "mark_completed"),
            patch.object(agent_worker, "build_result", return_value={"status": "FAILED"}),
            patch.object(
                agent_worker,
                "publish_slack_result",
                side_effect=lambda *a: events.append("slack"),
            ),
            patch.object(
                agent_worker,
                "finalize_browser_callback",
                side_effect=lambda *a, **k: events.append(("callback", k)),
            ),
        ):
            agent_worker.handle_execution_failure(
                job,
                Mock(),
                "logdir",
                failure_class="TIMEOUT",
                response_status="FAILED",
                artifact_status="NOT_RUN",
            )

        self.assertEqual(events[0], "slack")
        self.assertEqual(events[1][0], "callback")
        self.assertEqual(events[1][1]["failure_class"], "TIMEOUT")

    def test_no_callback_without_trustworthy_destination(self):
        with patch.object(agent_worker, "notify_chatgpt") as notify:
            result = agent_worker.send_browser_callback(
                callback_job(callback_type=None, callback_url=None),
                status="FAILED",
                artifact_status="NOT_RUN",
                failure_class="INVALID_JOB",
            )
        self.assertEqual(result["status"], "NOT_REQUESTED")
        notify.assert_not_called()

    def test_malformed_job_does_not_attempt_callback(self):
        say = Mock()
        with patch.object(agent_worker, "send_browser_callback") as callback:
            agent_worker.process_message(
                text='{"job_id":"BROKEN","callback":{"url":"https://chatgpt.com/c/x"}}',
                channel="C",
                sender="U",
                say=say,
            )
        say.assert_called_once()
        callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
