import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import Mock

import slack_bridge
from operational_logging import (
    BACKUP_COUNT,
    MAX_LOG_BYTES,
    configure_logging,
)


class OperationalLoggingTests(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        for handler in root.handlers[:]:
            if getattr(handler, "_local_agent_handler", False):
                root.removeHandler(handler)
                handler.close()

    def test_configuration_adds_console_and_rotating_file_handlers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "worker" / "worker.log"
            configured_path = configure_logging(path)
            handlers = [
                handler for handler in logging.getLogger().handlers
                if getattr(handler, "_local_agent_handler", False)
            ]
            rotating = [
                handler for handler in handlers
                if isinstance(handler, RotatingFileHandler)
            ]

            self.assertEqual(configured_path, path)
            self.assertTrue(path.parent.is_dir())
            self.assertEqual(len(handlers), 2)
            self.assertEqual(len(rotating), 1)
            self.assertEqual(rotating[0].maxBytes, MAX_LOG_BYTES)
            self.assertEqual(rotating[0].backupCount, BACKUP_COUNT)
            self.assertEqual(rotating[0].encoding.lower().replace("-", ""), "utf8")
            for handler in handlers:
                logging.getLogger().removeHandler(handler)
                handler.close()

    def test_authorized_raw_slack_payload_is_not_logged_at_info(self):
        bridge = slack_bridge.SlackBridge.__new__(slack_bridge.SlackBridge)
        bridge.message_handler = Mock()
        channel = next(iter(slack_bridge.ALLOWED_CHANNEL_IDS))
        sender = next(iter(slack_bridge.ALLOWED_SENDER_IDS))
        secret_prompt = "PROMPT-BODY-MUST-NOT-APPEAR"
        event = {
            "channel": channel,
            "user": sender,
            "text": (
                '{"protocol_version":"3","job_id":"JOB-LOG-1",'
                f'"Prompt":"{secret_prompt}"}}'
            ),
        }

        with self.assertLogs("slack_bridge", level="INFO") as captured:
            bridge._handle_message(event, Mock())

        output = "\n".join(captured.output)
        self.assertIn("JOB-LOG-1", output)
        self.assertIn("protocol_version=3", output)
        self.assertNotIn(secret_prompt, output)


if __name__ == "__main__":
    unittest.main()
