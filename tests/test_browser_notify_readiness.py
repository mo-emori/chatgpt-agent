import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from browser import notify


COMPOSER = "composer"
STOP = "stop"
SEND = "send"
USER_MESSAGE = "user-message"


class Element:
    def __init__(self, *, text="", enabled=True, on_click=None):
        self.text = text
        self.enabled = enabled
        self.fills = []
        self.clicks = 0
        self.on_click = on_click

    def is_visible(self):
        return True

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
    def __init__(self, composer, *, stops=None, sends=None, messages=None):
        self.url = "https://chatgpt.com/c/test"
        self.composer = composer
        self.stops = list(stops or [[]])
        self.sends = list(sends or [[]])
        self.messages = list(messages or [[], [Element(text="callback")]])
        self.waits = []
        for send_state in self.sends:
            for send in send_state:
                send.on_click = self._after_click

    def _after_click(self):
        if len(self.messages) > 1:
            self.messages.pop(0)

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
        return Locator([])

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        if len(self.stops) > 1:
            self.stops.pop(0)
        if len(self.sends) > 1:
            self.sends.pop(0)


class BrowserNotifyReadinessTests(unittest.TestCase):
    def run_notify(self, page, *, monotonic=None):
        browser = Mock()
        browser.contexts = [Mock(pages=[page])]
        playwright = Mock()
        playwright.chromium.connect_over_cdp.return_value = browser
        dom = {
            "composer": {"selectors": [COMPOSER]},
            "stop_button": {"selectors": [STOP]},
            "send_button": {"selectors": [SEND]},
            "user_message": {"selectors": [USER_MESSAGE]},
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
                        target_url=page.url, message="callback"
                    )
            return notify.notify_chatgpt(target_url=page.url, message="callback")

    def test_idle_empty_fills_and_clicks_send(self):
        composer = Element()
        send = Element()
        page = Page(composer, sends=[[send]])

        self.run_notify(page)

        self.assertEqual(composer.fills, ["callback"])
        self.assertEqual(send.clicks, 1)

    def test_click_without_new_matching_message_is_delivery_unknown(self):
        composer = Element()
        send = Element()
        page = Page(composer, sends=[[send]], messages=[[]])
        send.on_click = lambda: setattr(composer, "text", "user replacement")

        with self.assertRaisesRegex(notify.BrowserNotifyError, "DELIVERY_UNKNOWN"):
            self.run_notify(page, monotonic=[0, 0, 0, 11])

        self.assertEqual(send.clicks, 1)
        self.assertEqual(composer.fills, ["callback"])
        self.assertEqual(composer.text, "user replacement")

    def test_old_identical_message_does_not_acknowledge_delivery(self):
        composer = Element()
        send = Element()
        old = Element(text="callback")
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
        self.assertEqual(composer.fills, ["callback"])

    def test_new_identical_message_after_old_one_confirms_delivery(self):
        composer = Element()
        send = Element()
        old = Element(text="callback")
        new = Element(text="callback")
        page = Page(
            composer, sends=[[send]], messages=[[old], [old, new]]
        )

        self.run_notify(page)

        self.assertEqual(send.clicks, 1)

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

    def test_generating_waits_for_stop_to_disappear_then_sends(self):
        composer = Element()
        stop = Element()
        send = Element()
        page = Page(composer, stops=[[stop], []], sends=[[send]])

        self.run_notify(page)

        self.assertEqual(page.waits, [500])
        self.assertEqual(composer.fills, ["callback"])
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

        self.assertEqual(composer.fills, ["callback"])
        self.assertEqual(send.clicks, 1)

    def test_send_timeout_attempts_callback_cleanup(self):
        composer = Element()
        page = Page(composer)

        with self.assertRaisesRegex(notify.BrowserNotifyError, "SEND_BUTTON_TIMEOUT"):
            self.run_notify(page, monotonic=[0, 0, 6])

        self.assertEqual(composer.fills, ["callback", ""])

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

        self.assertEqual(composer.fills, ["callback", ""])
        self.assertTrue(all(send.clicks == 0 for send in sends))

    def test_cleanup_does_not_clear_user_replacement(self):
        composer = Element(text="user replacement")
        page = Page(composer)

        notify.cleanup_inserted_callback(page, [COMPOSER], "callback")

        self.assertEqual(composer.fills, [])


if __name__ == "__main__":
    unittest.main()
