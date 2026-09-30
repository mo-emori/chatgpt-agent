import json
import logging

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from config import (
    ALLOWED_CHANNEL_IDS,
    ALLOWED_SENDER_IDS,
    SLACK_APP_TOKEN,
    SLACK_BOT_TOKEN,
)

logger = logging.getLogger(__name__)


class SlackBridge:
    def __init__(self, message_handler):
        self.app = App(token=SLACK_BOT_TOKEN)
        self.message_handler = message_handler

        self.app.event("message")(
            self._handle_message
        )

    def _handle_message(self, event, say):
        if event.get("subtype") is not None:
            return

        channel = event.get("channel")
        sender = event.get("user")

        # Authorization
        if channel not in ALLOWED_CHANNEL_IDS:
            logger.warning("Slack message ignored: unauthorized channel %s", channel)
            return

        if sender not in ALLOWED_SENDER_IDS:
            logger.warning("Slack message ignored: unauthorized sender %s", sender)
            return

        raw_text = event.get("text", "")

        text = raw_text.strip()

        if (
            text == "LOCAL-AGENT PING"
            or text.startswith("LOCAL-AGENT PING *")
        ):
            logger.info(
                "Accepted LOCAL-AGENT PING: channel=%s sender=%s",
                channel,
                sender,
            )
            say("LOCAL-AGENT PONG — Worker ready")
            return

        metadata = {}
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                metadata = payload
        except (json.JSONDecodeError, TypeError):
            pass
        logger.info(
            "Authorized Slack event: channel=%s sender=%s job_id=%s protocol_version=%s",
            channel, sender, metadata.get("job_id", "unknown"),
            metadata.get("protocol_version", "unknown"),
        )
        logger.debug("Authorized Slack event raw text: %r", text)

        self.message_handler(
            text=text,
            channel=channel,
            sender=sender,
            say=say,
        )

    def start(self):
        SocketModeHandler(
            self.app,
            SLACK_APP_TOKEN,
        ).start()

    def say(self, message):
        # Startup recovery has no Slack event-bound say callback. Jobs are
        # accepted only from the configured channel set, which is currently
        # the durable routing information available to the worker.
        channel = next(iter(ALLOWED_CHANNEL_IDS))
        self.app.client.chat_postMessage(
            channel=channel,
            text=message,
        )
