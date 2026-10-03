import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from browser import notify


COMPOSER = "composer"
STOP = "stop"
SEND = "send"
CURRENT_USER_MESSAGE = "article[data-testid^='conversation-turn-'][data-turn='user']"
LEGACY_USER_MESSAGE = "[data-message-author-role='user']"
USER_MESSAGE = CURRENT_USER_MESSAGE
CALLBACK = (
    "LOCAL_AGENT_JOB_COMPLETED\n\n"
    "job_id: JOB-ACK-001\n"
    "actor: codex\n"
    "workspace: sandbox\n"
    "status: DONE\n"
    "artifact_status: DONE"
)


class Element:
    def __init__(self, *, text="", enabled=True, on_click=None, visible=True,
                 tag_name="DIV", contenteditable="true", has_value=False,
                 readback=None, snapshot_data=None):
        self.text = text
        self.enabled = enabled
        self.fills = []
        self.clicks = 0
        self.on_click = on_click
        self.visible = visible
        self.tag_name = tag_name
        self.contenteditable = contenteditable
        self.has_value = has_value
        self.readback = readback
        self.snapshot_data = snapshot_data or {}
        self.page = None
        self.presses = []

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def evaluate(self, script):
        if "tagName" in script:
            text = (
                self.readback
                if self.readback is not None and self.fills and self.fills[-1] != ""
                else self.text
            )
            data = {
                "tagName": self.tag_name,
                "contenteditable": self.contenteditable,
                "hasValue": self.has_value,
                "value": text if self.has_value else None,
                "innerText": text,
                "textContent": text,
                "rangeText": text,
                "plainText": text,
                "extractionMode": "inline-tree",
                "childNodes": [],
                "childNodeCount": 0,
            }
            data.update(self.snapshot_data)
            return data
        return self.text

    def fill(self, value):
        self.fills.append(value)
        self.text = value

    def focus(self):
        self.page.focused = self

    def press(self, key):
        self.presses.append(key)
        if key == "Backspace" and self.presses[-2:] == ["ControlOrMeta+A", "Backspace"]:
            self.text = ""
            self.readback = None

    def click(self):
        self.clicks += 1
        if self.on_click is not None:
            self.on_click()


class Locator:
    def __init__(self, elements):
        self.elements = elements

    def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]


class Page:
    def __init__(self, composer, *, stops=None, sends=None, messages=None,
                 legacy_messages=None, other_elements=None):
        self.url = "https://chatgpt.com/c/test"
        self.composer = composer
        self.stops = list(stops or [[]])
        self.sends = list(sends or [[]])
        self.messages = list(messages or [[], [Element(text=CALLBACK)]])
        self.legacy_messages = list(legacy_messages or [[]])
        self.other_elements = dict(other_elements or {})
        self.waits = []
        self.focused = None
        self.keyboard = Keyboard(self)
        composer.page = self
        for send_state in self.sends:
            for send in send_state:
                send.on_click = self._after_click

    def _after_click(self):
        if len(self.messages) > 1:
            self.messages.pop(0)
        if len(self.legacy_messages) > 1:
            self.legacy_messages.pop(0)

    def bring_to_front(self):
        pass

    def locator(self, selector):
        if selector == COMPOSER:
            return Locator([self.composer])
        if selector == STOP:
            return Locator(self.stops[0])
        if selector == SEND:
            return Locator(self.sends[0])
        if selector == USER_MESSAGE:
            return Locator(self.messages[0])
        if selector == LEGACY_USER_MESSAGE:
            return Locator(self.legacy_messages[0])
        if selector in self.other_elements:
            return Locator(self.other_elements[selector])
        return Locator([])

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        if len(self.stops) > 1:
            self.stops.pop(0)
        if len(self.sends) > 1:
            self.sends.pop(0)


class Keyboard:
    def __init__(self, page):
        self.page = page
        self.insertions = []

    def insert_text(self, value):
        self.insertions.append(value)
        element = self.page.focused
        element.text = element.readback if element.readback is not None else value


class BrowserNotifyReadinessTests(unittest.TestCase):
    def run_notify(self, page, *, monotonic=None, message=CALLBACK):
        browser = Mock()
        browser.contexts = [Mock(pages=[page])]
        playwright = Mock()
        playwright.chromium.connect_over_cdp.return_value = browser
        dom = {
            "composer": {"selectors": [COMPOSER]},
            "stop_button": {"selectors": [STOP]},
            "send_button": {"selectors": [SEND]},
            "user_message": {"selectors": [CURRENT_USER_MESSAGE, LEGACY_USER_MESSAGE]},
        }
        patches = [
            patch.object(notify, "BROWSER_CONFIG", {
                "enabled": True, "cdp_url": "http://cdp"
            }),
            patch.object(notify, "load_dom_config", return_value=dom),
            patch.object(notify, "sync_playwright", return_value=nullcontext(playwright)),
        ]
        if monotonic is not None:
            patches.append(patch.object(notify.time, "monotonic", side_effect=monotonic))

        with patches[0], patches[1], patches[2]:
            if len(patches) == 4:
                with patches[3]:
                    return notify.notify_chatgpt(
                        target_url=page.url, message=message
                    )
            return notify.notify_chatgpt(target_url=page.url, message=message)

    def test_idle_empty_fills_and_clicks_send(self):
        composer = Element()
        send = Element()
        page = Page(composer, sends=[[send]])

        self.run_notify(page)

        self.assertEqual(page.keyboard.insertions, [CALLBACK])
        self.assertEqual(send.clicks, 1)

    def test_contenteditable_ignores_incidental_value_property(self):
        composer = Element(has_value=True)
        send = Element()
        page = Page(composer, sends=[[send]])

        self.run_notify(page)

        self.assertEqual(send.clicks, 1)

    def test_contenteditable_crlf_readback_is_rejected(self):
        composer = Element(readback=CALLBACK.replace("\n", "\r\n"))
        send = Element()
        page = Page(composer, sends=[[send]])

        with self.assertRaisesRegex(
            notify.BrowserNotifyError, "COMPOSER_FILL_NOT_VERIFIED"
        ):
            self.run_notify(page)

        self.assertEqual(send.clicks, 0)

    def test_textarea_fill_and_newline_normalization_still_work(self):
        composer = Element(
            tag_name="TEXTAREA", contenteditable=None, has_value=True,
            readback=CALLBACK.replace("\n", "\r\n"),
        )
        send = Element()
        page = Page(composer, sends=[[send]])

        self.run_notify(page)

        self.assertEqual(composer.fills, [CALLBACK])
        self.assertEqual(page.keyboard.insertions, [])
        self.assertEqual(send.clicks, 1)

    def test_input_uses_fill_and_value(self):
        composer = Element(tag_name="INPUT", contenteditable=None, has_value=True)
        send = Element()
        page = Page(composer, sends=[[send]])

        self.run_notify(page)

        self.assertEqual(len(composer.fills), 1)
        self.assertEqual(page.keyboard.insertions, [])
        self.assertEqual(send.clicks, 1)

    def test_prosemirror_block_lines_make_inner_text_inflate_but_extract_exactly(self):
        expected = (
            "LOCAL_AGENT_JOB_COMPLETED\njob_id: J\n\nactor: codex\n"
            "workspace: x\n\nstatus: DONE\nartifact_status: DONE\nartifacts: []"
        )
        inflated = expected.replace("\n", "\n\n")
        inflated = inflated.replace(
            "LOCAL_AGENT_JOB_COMPLETED\n\n",
            "LOCAL_AGENT_JOB_COMPLETED\n\n\n\n",
            1,
        )
        lines = expected.split("\n")
        composer = Element(snapshot_data={
            "innerText": inflated,
            "textContent": "".join(lines),
            "rangeText": "".join(lines),
            "plainText": expected,
            "extractionMode": "direct-block-lines",
            "childNodes": [
                {
                    "node_type": 1,
                    "tag_name": "P",
                    "text_length": len(line),
                    "child_count": 1,
                    "child_tags": ["BR" if line == "" else "#text"],
                }
                for line in lines
            ],
            "childNodeCount": len(lines),
        })

        snapshot = notify.composer_snapshot(composer)

        self.assertEqual(expected.count("\n"), 8)
        self.assertEqual(snapshot["inner_text"].count("\n"), 18)
        self.assertEqual(snapshot["text_content"].count("\n"), 0)
        self.assertEqual(snapshot["range_text"].count("\n"), 0)
        self.assertEqual(snapshot["plain_text"], expected)
        self.assertTrue(notify.composer_fill_matches(
            snapshot["plain_text"], expected, kind="contenteditable"
        ))

    def test_structural_diagnostics_do_not_include_callback_text(self):
        snapshot = {
            "tag_name": "DIV", "contenteditable": "true", "has_value": False,
            "extraction_mode": "direct-block-lines", "child_node_count": 2,
            "child_nodes": [{"node_type": 1, "tag_name": "P", "text_length": 7}],
            "inner_text": CALLBACK.replace("\n", "\n\n"),
            "text_content": CALLBACK.replace("\n", ""),
            "range_text": CALLBACK.replace("\n", ""),
        }

        diagnostic = notify.composer_fill_diagnostics(snapshot, CALLBACK, CALLBACK)

        self.assertNotIn(CALLBACK, repr(diagnostic))
        self.assertTrue(diagnostic["plain_text"]["matches"])
        self.assertEqual(diagnostic["expected_blank_line_positions"], [1])

    def test_contenteditable_insert_text_preserves_multiple_blank_lines(self):
        message = CALLBACK + "\n\n\nfinal"
        composer = Element()
        send = Element()
        page = Page(composer, sends=[[send]])

        self.run_notify(page, message=message)

        self.assertEqual(page.keyboard.insertions, [message])
        self.assertEqual(composer.text, message)
        self.assertEqual(send.clicks, 1)

    def test_truncated_readback_fails_verification_without_click_and_cleans_up(self):
        composer = Element(readback=CALLBACK[:-1])
        send = Element()
        page = Page(composer, sends=[[send]])

        with self.assertLogs(notify.logger, level="ERROR") as captured:
            with self.assertRaisesRegex(
                notify.BrowserNotifyError, "COMPOSER_FILL_NOT_VERIFIED"
            ):
                self.run_notify(page)

        self.assertEqual(send.clicks, 0)
        self.assertEqual(page.keyboard.insertions, [CALLBACK])
        self.assertEqual(composer.presses, ["ControlOrMeta+A", "Backspace"])
        self.assertEqual(composer.text, "")
        self.assertIn("'plain_text': {'length': 112", " ".join(captured.output))

    def test_wrong_identity_readback_fails_without_click(self):
        composer = Element(readback=CALLBACK.replace("JOB-ACK-001", "JOB-ACK-002"))
        send = Element()
        page = Page(composer, sends=[[send]])

        with self.assertRaisesRegex(
            notify.BrowserNotifyError, "COMPOSER_FILL_NOT_VERIFIED"
        ):
            self.run_notify(page)

        self.assertEqual(send.clicks, 0)
        self.assertEqual(composer.presses, ["ControlOrMeta+A", "Backspace"])

    def test_material_mutation_readback_fails_without_click(self):
        composer = Element(readback=CALLBACK.replace("status: DONE", "status: FAILED"))
        send = Element()
        page = Page(composer, sends=[[send]])

        with self.assertRaisesRegex(
            notify.BrowserNotifyError, "COMPOSER_FILL_NOT_VERIFIED"
        ):
            self.run_notify(page)

        self.assertEqual(send.clicks, 0)
        self.assertEqual(composer.presses, ["ControlOrMeta+A", "Backspace"])

    def test_click_without_new_matching_message_is_delivery_unknown(self):
        composer = Element()
        send = Element()
        page = Page(composer, sends=[[send]], messages=[[]])
        send.on_click = lambda: setattr(composer, "text", "user replacement")

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

        self.assertEqual(send.clicks, 1)
        self.assertEqual(page.keyboard.insertions, [CALLBACK])
        self.assertEqual(composer.text, "user replacement")

    def test_old_identical_message_does_not_acknowledge_delivery(self):
        composer = Element()
        send = Element()
        old = Element(text=CALLBACK)
        page = Page(composer, sends=[[send]], messages=[[old]])

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

        self.assertEqual(send.clicks, 1)

    def test_click_exception_is_unknown_and_does_not_cleanup_or_retry(self):
        composer = Element()
        send = Element()
        page = Page(composer, sends=[[send]])
        send.on_click = Mock(side_effect=RuntimeError("CDP response lost"))

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page)

        self.assertEqual(send.clicks, 1)
        self.assertEqual(page.keyboard.insertions, [CALLBACK])

    def test_new_identical_message_after_old_one_confirms_delivery(self):
        composer = Element()
        send = Element()
        old = Element(text=CALLBACK)
        new = Element(text=CALLBACK)
        page = Page(
            composer, sends=[[send]], messages=[[old], [old, new]]
        )

        self.run_notify(page)

        self.assertEqual(send.clicks, 1)

    def test_legacy_user_message_selector_remains_compatible(self):
        composer = Element()
        send = Element()
        page = Page(
            composer, sends=[[send]], messages=[[]],
            legacy_messages=[[], [Element(text=CALLBACK)]],
        )

        self.run_notify(page)

        self.assertEqual(send.clicks, 1)

    def test_hidden_current_user_message_cannot_confirm(self):
        page = Page(
            Element(), sends=[[Element()]],
            messages=[[], [Element(text=CALLBACK, visible=False)]],
        )

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

    def test_assistant_history_composer_and_unrelated_text_cannot_confirm(self):
        composer = Element()
        historical = Element(text=CALLBACK)
        page = Page(
            composer, sends=[[Element()]],
            messages=[[historical], [historical, Element(text="unrelated user text")]],
            other_elements={"assistant-message": [Element(text=CALLBACK)]},
        )

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

    def test_new_identity_with_rendered_whitespace_differences_confirms(self):
        composer = Element()
        send = Element()
        rendered = (
            "LOCAL_AGENT_JOB_COMPLETED   job_id:   JOB-ACK-001\r\n"
            "actor: codex workspace: sandbox status: DONE artifact_status: DONE"
        )
        page = Page(composer, sends=[[send]], messages=[[], [Element(text=rendered)]])

        self.run_notify(page)

        self.assertEqual(send.clicks, 1)

    def test_new_job_id_only_does_not_confirm(self):
        page = Page(Element(), sends=[[Element()]], messages=[[], [
            Element(text="job_id: JOB-ACK-001 actor: codex")
        ]])

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

    def test_new_marker_only_does_not_confirm(self):
        page = Page(Element(), sends=[[Element()]], messages=[[], [
            Element(text="LOCAL_AGENT_JOB_COMPLETED")
        ]])

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

    def test_marker_and_job_id_in_different_new_nodes_do_not_confirm(self):
        page = Page(Element(), sends=[[Element()]], messages=[[], [
            Element(text="LOCAL_AGENT_JOB_COMPLETED"),
            Element(text="job_id: JOB-ACK-001 actor: codex"),
        ]])

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

    def test_wrong_job_id_does_not_confirm(self):
        page = Page(Element(), sends=[[Element()]], messages=[[], [Element(text=(
            "LOCAL_AGENT_JOB_COMPLETED job_id: JOB-ACK-002 actor: codex"
        ))]])

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

    def test_composer_empty_without_matching_message_is_not_confirmed(self):
        composer = Element()
        send = Element(on_click=lambda: setattr(composer, "text", ""))
        page = Page(composer, sends=[[send]], messages=[[]])
        send.on_click = lambda: setattr(composer, "text", "")

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

    def test_stop_visible_without_matching_message_is_not_confirmed(self):
        composer = Element()
        send = Element()
        stop = Element()
        page = Page(composer, sends=[[send]], messages=[[]])
        send.on_click = lambda: page.stops.__setitem__(0, [stop])

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

        self.assertEqual(stop.clicks, 0)

    def test_composer_empty_and_stop_without_selector_is_submitted_unverified(self):
        composer = Element()
        send = Element()
        stop = Element()
        page = Page(composer, sends=[[send]], messages=[[]])
        def after_click():
            composer.text = ""
            page.stops[0] = [stop]
        send.on_click = after_click

        result = self.run_notify(page, monotonic=[0, 0, 0, 11])

        self.assertEqual(result, notify.SUBMITTED_ACK_UNVERIFIED)
        self.assertEqual(send.clicks, 1)

    def test_wrong_identity_with_submission_transition_is_unverified_not_confirmed(self):
        composer = Element()
        stop = Element()
        page = Page(composer, sends=[[Element()]], messages=[[], [Element(text=(
            "LOCAL_AGENT_JOB_COMPLETED job_id: JOB-ACK-002 actor: codex"
        ))]])
        def after_click():
            composer.text = ""
            page.stops[0] = [stop]
            page.messages.pop(0)
        page.sends[0][0].on_click = after_click

        result = self.run_notify(page, monotonic=[0, 0, 0, 11])

        self.assertEqual(result, notify.SUBMITTED_ACK_UNVERIFIED)

    def test_generating_waits_for_stop_to_disappear_then_sends(self):
        composer = Element()
        stop = Element()
        send = Element()
        page = Page(composer, stops=[[stop], []], sends=[[send]])

        self.run_notify(page)

        self.assertEqual(page.waits, [500])
        self.assertEqual(page.keyboard.insertions, [CALLBACK])
        self.assertEqual(send.clicks, 1)
        self.assertEqual(stop.clicks, 0)

    def test_stop_timeout_does_not_fill_or_click_stop(self):
        composer = Element()
        stop = Element()
        page = Page(composer, stops=[[stop]])

        with self.assertRaisesRegex(notify.BrowserNotifyError, "CHATGPT_NOT_READY_TIMEOUT"):
            self.run_notify(page, monotonic=[0, 0, 121])

        self.assertEqual(composer.fills, [])
        self.assertEqual(stop.clicks, 0)

    def test_user_draft_fails_closed_without_overwrite(self):
        composer = Element(text="human draft")
        page = Page(composer)

        with self.assertRaisesRegex(notify.BrowserNotifyError, "COMPOSER_NOT_EMPTY"):
            self.run_notify(page)

        self.assertEqual(composer.text, "human draft")
        self.assertEqual(composer.fills, [])

    def test_whitespace_only_contenteditable_is_empty(self):
        composer = Element(text="\n")
        send = Element()
        page = Page(composer, sends=[[send]])

        self.run_notify(page)

        self.assertEqual(page.keyboard.insertions, [CALLBACK])
        self.assertEqual(send.clicks, 1)

    def test_send_timeout_attempts_callback_cleanup(self):
        composer = Element()
        page = Page(composer)

        with self.assertRaisesRegex(notify.BrowserNotifyError, "SEND_BUTTON_TIMEOUT"):
            self.run_notify(page, monotonic=[0, 0, 6])

        self.assertEqual(page.keyboard.insertions, [CALLBACK])
        self.assertEqual(composer.presses, ["ControlOrMeta+A", "Backspace"])

    def test_multiple_stop_buttons_fails_closed(self):
        composer = Element()
        stops = [Element(), Element()]
        page = Page(composer, stops=[stops])

        with self.assertRaisesRegex(notify.BrowserNotifyError, "stop_button: multiple"):
            self.run_notify(page)

        self.assertEqual(composer.fills, [])
        self.assertTrue(all(stop.clicks == 0 for stop in stops))

    def test_multiple_send_buttons_fails_closed_and_cleans_up(self):
        composer = Element()
        sends = [Element(), Element()]
        page = Page(composer, sends=[sends])

        with self.assertRaisesRegex(notify.BrowserNotifyError, "send_button: multiple"):
            self.run_notify(page)

        self.assertEqual(page.keyboard.insertions, [CALLBACK])
        self.assertEqual(composer.presses, ["ControlOrMeta+A", "Backspace"])
        self.assertTrue(all(send.clicks == 0 for send in sends))

    def test_cleanup_does_not_clear_user_replacement(self):
        composer = Element(text="user replacement")
        page = Page(composer)

        notify.cleanup_inserted_callback(page, [COMPOSER], "callback")

        self.assertEqual(composer.fills, [])


if __name__ == "__main__":
    unittest.main()
