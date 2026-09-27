from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from config import (
    ALLOWED_CHANNEL_IDS,
    ALLOWED_SENDER_IDS,
    SLACK_APP_TOKEN,
    SLACK_BOT_TOKEN,
)


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
            print(
                "Slack message ignored:"
                " unauthorized channel",
                channel,
            )
            return

        if sender not in ALLOWED_SENDER_IDS:
            print(
                "Slack message ignored:"
                " unauthorized sender",
                sender,
            )
            return

        text = event.get("text", "").strip()

        print()
        print("===== AUTHORIZED SLACK EVENT =====")
        print("channel:", channel)
        print("sender :", sender)
        print("text   :", repr(text))
        print("==================================")

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