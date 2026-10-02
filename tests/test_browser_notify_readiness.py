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
    def __init__(self, *, text="", enabled=True, on_click=None, visible=True):
        self.text = text
        self.enabled = enabled
        self.fills = []
        self.clicks = 0
        self.on_click = on_click
        self.visible = visible

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def evaluate(self, _script):
        return self.text

    def fill(self, value):
        self.fills.append(value)
        self.text = value

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

        self.assertEqual(composer.fills, [CALLBACK])
        self.assertEqual(send.clicks, 1)

    def test_click_without_new_matching_message_is_delivery_unknown(self):
        composer = Element()
        send = Element()
        page = Page(composer, sends=[[send]], messages=[[]])
        send.on_click = lambda: setattr(composer, "text", "user replacement")

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

        self.assertEqual(send.clicks, 1)
        self.assertEqual(composer.fills, [CALLBACK])
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
        self.assertEqual(composer.fills, [CALLBACK])

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

    def test_composer_empty_and_stop_without_combined_identity_do_not_confirm(self):
        composer = Element()
        send = Element()
        stop = Element()
        page = Page(composer, sends=[[send]], messages=[[], [
            Element(text="LOCAL_AGENT_JOB_COMPLETED")
        ]])
        def after_click():
            composer.text = ""
            page.stops[0] = [stop]
            page.messages.pop(0)
        send.on_click = after_click

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

        self.assertEqual(send.clicks, 1)

    def test_generating_waits_for_stop_to_disappear_then_sends(self):
        composer = Element()
        stop = Element()
        send = Element()
        page = Page(composer, stops=[[stop], []], sends=[[send]])

        self.run_notify(page)

        self.assertEqual(page.waits, [500])
        self.assertEqual(composer.fills, [CALLBACK])
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

        self.assertEqual(composer.fills, [CALLBACK])
        self.assertEqual(send.clicks, 1)

    def test_send_timeout_attempts_callback_cleanup(self):
        composer = Element()
        page = Page(composer)

        with self.assertRaisesRegex(notify.BrowserNotifyError, "SEND_BUTTON_TIMEOUT"):
            self.run_notify(page, monotonic=[0, 0, 6])

        self.assertEqual(composer.fills, [CALLBACK, ""])

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

        self.assertEqual(composer.fills, [CALLBACK, ""])
        self.assertTrue(all(send.clicks == 0 for send in sends))

    def test_cleanup_does_not_clear_user_replacement(self):
        composer = Element(text="user replacement")
        page = Page(composer)

        notify.cleanup_inserted_callback(page, [COMPOSER], "callback")

        self.assertEqual(composer.fills, [])


if __name__ == "__main__":
    unittest.main()
