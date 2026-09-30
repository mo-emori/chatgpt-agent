import json
import logging
import time
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
)

from config import BROWSER_CONFIG


logger = logging.getLogger(__name__)

CHATGPT_READY_TIMEOUT_SECONDS = 120
SEND_BUTTON_TIMEOUT_SECONDS = 5
STATE_POLL_INTERVAL_MS = 500


DOM_CONFIG_FILE = (
    Path(__file__).parent
    / "chatgpt_dom.json"
)


class BrowserNotifyError(RuntimeError):
    pass


def load_dom_config():
    return json.loads(
        DOM_CONFIG_FILE.read_text(
            encoding="utf-8"
        )
    )


def find_unique_visible(
    page,
    selectors,
    *,
    name,
):
    for selector in selectors:
        locator = page.locator(
            selector
        )

        visible = []

        for i in range(
            locator.count()
        ):
            item = locator.nth(i)

            if item.is_visible():
                visible.append(item)

        if len(visible) == 1:
            return visible[0]

        if len(visible) > 1:
            raise BrowserNotifyError(
                f"{name}: multiple "
                f"visible elements"
            )

    raise BrowserNotifyError(
        f"{name}: not found"
    )


def find_optional_unique_visible(page, selectors, *, name):
    for selector in selectors:
        locator = page.locator(selector)
        visible = [
            locator.nth(i)
            for i in range(locator.count())
            if locator.nth(i).is_visible()
        ]

        if len(visible) == 1:
            return visible[0]

        if len(visible) > 1:
            raise BrowserNotifyError(
                f"{name}: multiple visible elements"
            )

    return None


def wait_until_chatgpt_ready(page, stop_selectors):
    deadline = time.monotonic() + CHATGPT_READY_TIMEOUT_SECONDS
    waited = False

    while True:
        stop_button = find_optional_unique_visible(
            page, stop_selectors, name="stop_button"
        )
        if stop_button is None:
            if waited:
                logger.info("ChatGPT ready after generation wait")
            return

        if not waited:
            logger.info("Waiting because ChatGPT is generating")
            waited = True

        if time.monotonic() >= deadline:
            raise BrowserNotifyError("CHATGPT_NOT_READY_TIMEOUT")

        page.wait_for_timeout(STATE_POLL_INTERVAL_MS)


def wait_for_send_button(page, selectors):
    deadline = time.monotonic() + SEND_BUTTON_TIMEOUT_SECONDS

    while True:
        send_button = find_optional_unique_visible(
            page, selectors, name="send_button"
        )
        if send_button is not None:
            return send_button

        if time.monotonic() >= deadline:
            raise BrowserNotifyError("SEND_BUTTON_TIMEOUT")

        page.wait_for_timeout(STATE_POLL_INTERVAL_MS)


def composer_text(composer):
    value = composer.evaluate(
        "element => 'value' in element ? element.value : element.innerText"
    )
    return value or ""


def composer_is_empty(composer):
    return composer_text(composer).strip() == ""


def cleanup_inserted_callback(page, composer_selectors, message):
    try:
        composer = find_optional_unique_visible(
            page, composer_selectors, name="composer"
        )
        if composer is not None and composer_text(composer) == message:
            composer.fill("")
    except Exception:
        logger.warning("Browser callback composer cleanup failed", exc_info=True)


def find_target_page(
    browser,
    target_url,
):
    matches = []

    # trailing slash程度の差を吸収
    normalized = target_url.rstrip("/")

    for context in browser.contexts:
        for page in context.pages:
            if (
                page.url.rstrip("/")
                == normalized
            ):
                matches.append(page)

    if len(matches) == 0:
        raise BrowserNotifyError(
            "TARGET_CHAT_NOT_FOUND"
        )

    if len(matches) > 1:
        raise BrowserNotifyError(
            "TARGET_CHAT_AMBIGUOUS"
        )

    return matches[0]


def notify_chatgpt(
    *,
    target_url,
    message,
):
    if not BROWSER_CONFIG.get(
        "enabled",
        False,
    ):
        raise BrowserNotifyError(
            "BROWSER_CALLBACK_DISABLED"
        )

    cdp_url = BROWSER_CONFIG[
        "cdp_url"
    ]

    try:
        with sync_playwright() as p:
            browser = (
                p.chromium
                .connect_over_cdp(
                    cdp_url
                )
            )

            page = find_target_page(
                browser,
                target_url,
            )

            page.bring_to_front()

            dom = load_dom_config()

            composer = (
                find_unique_visible(
                    page,
                    dom[
                        "composer"
                    ]["selectors"],
                    name="composer",
                )
            )

            wait_until_chatgpt_ready(
                page,
                dom["stop_button"]["selectors"],
            )

            composer = find_unique_visible(
                page,
                dom["composer"]["selectors"],
                name="composer",
            )
            if not composer_is_empty(composer):
                raise BrowserNotifyError("COMPOSER_NOT_EMPTY")

            filled = False
            try:
                composer.fill(message)
                filled = True

                send_button = wait_for_send_button(
                    page,
                    dom["send_button"]["selectors"],
                )
                if not send_button.is_enabled():
                    raise BrowserNotifyError("SEND_BUTTON_DISABLED")

                send_button.click()
            except Exception:
                if filled:
                    cleanup_inserted_callback(
                        page,
                        dom["composer"]["selectors"],
                        message,
                    )
                raise

    except BrowserNotifyError:
        raise

    except Exception as e:
        raise BrowserNotifyError(
            f"BROWSER_CALLBACK_FAILED: {e}"
        ) from e
