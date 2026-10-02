import json
import logging
import re
import time
from collections import Counter
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
)

from config import BROWSER_CONFIG


logger = logging.getLogger(__name__)

CHATGPT_READY_TIMEOUT_SECONDS = 120
SEND_BUTTON_TIMEOUT_SECONDS = 5
DELIVERY_ACK_TIMEOUT_SECONDS = 10
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


def element_text(element):
    value = element.evaluate("element => element.innerText")
    return (value or "").replace("\r\n", "\n")


def visible_user_messages(page, selectors):
    for selector in selectors:
        locator = page.locator(selector)
        elements = [
            locator.nth(index)
            for index in range(locator.count())
            if locator.nth(index).is_visible()
        ]
        if elements:
            return selector, elements

    return None, []


def user_message_snapshot(page, selectors):
    strategy, elements = visible_user_messages(page, selectors)
    return strategy, [element_text(element) for element in elements]


def callback_identity(message):
    marker = None
    job_id = None
    for line in message.splitlines():
        stripped = line.strip()
        if stripped in {"LOCAL_AGENT_JOB_COMPLETED", "LOCAL_AGENT_JOB_FAILED"}:
            marker = stripped
        elif stripped.startswith("job_id: "):
            job_id = stripped.removeprefix("job_id: ").strip()

    if marker is None or not job_id:
        raise BrowserNotifyError("INVALID_CALLBACK_IDENTITY")

    return marker, job_id


def normalized_rendered_text(value):
    return " ".join(value.split())


def callback_identity_matches(text, marker, job_id):
    rendered = normalized_rendered_text(text)
    marker_match = re.search(
        rf"(?:^|\s){re.escape(marker)}(?=\s|$)", rendered
    ) is not None
    job_id_match = re.search(
        rf"(?:^|\s)job_id:\s*{re.escape(normalized_rendered_text(job_id))}"
        rf"(?=\s+actor:|$)",
        rendered,
    ) is not None
    return marker_match, job_id_match


def callback_log_identity(message):
    safe = {}
    for line in message.splitlines():
        if line.startswith("LOCAL_AGENT_JOB_"):
            safe["marker"] = line
        elif line.startswith("job_id: "):
            safe["job_id"] = line.removeprefix("job_id: ")
        elif line.startswith("status: "):
            safe["status"] = line.removeprefix("status: ")
    return " ".join(f"{key}={value}" for key, value in safe.items())


def wait_for_delivery_ack(
    page, *, composer_selectors, stop_selectors, user_message_selectors,
    message, user_messages_before,
):
    deadline = time.monotonic() + DELIVERY_ACK_TIMEOUT_SECONDS
    saw_composer_empty = False
    saw_stop_visible = False
    marker, job_id = callback_identity(message)
    marker_match_seen = False
    job_id_match_seen = False
    combined_identity_match_seen = False
    selector_before, texts_before = user_messages_before
    user_message_count_before = len(texts_before)
    selector_after = selector_before
    user_message_count_after = user_message_count_before

    while True:
        selector_after, texts_after = user_message_snapshot(
            page, user_message_selectors
        )
        user_message_count_after = len(texts_after)
        remaining_before = Counter(texts_before)
        new_texts = []
        for text in texts_after:
            if remaining_before[text]:
                remaining_before[text] -= 1
            else:
                new_texts.append(text)
        for text in new_texts:
            marker_match, job_id_match = callback_identity_matches(
                text, marker, job_id
            )
            marker_match_seen = marker_match_seen or marker_match
            job_id_match_seen = job_id_match_seen or job_id_match
            combined_identity_match_seen = (
                combined_identity_match_seen or (marker_match and job_id_match)
            )

        new_user_message_count = len(new_texts)
        if combined_identity_match_seen:
            logger.info(
                "Browser callback DELIVERY_CONFIRMED: %s "
                "user_message_selector_before=%r user_message_selector_after=%r "
                "user_message_count_before=%s user_message_count_after=%s "
                "new_user_message_count=%s marker_match_seen=%s "
                "job_id_match_seen=%s combined_identity_match_seen=%s "
                "composer_empty_seen=%s stop_seen=%s timeout_seconds=%s",
                callback_log_identity(message), selector_before, selector_after,
                user_message_count_before,
                user_message_count_after, new_user_message_count,
                marker_match_seen, job_id_match_seen,
                combined_identity_match_seen, saw_composer_empty,
                saw_stop_visible, DELIVERY_ACK_TIMEOUT_SECONDS,
            )
            return

        try:
            composer = find_optional_unique_visible(
                page, composer_selectors, name="composer"
            )
            saw_composer_empty = saw_composer_empty or (
                composer is not None and composer_is_empty(composer)
            )
            stop_button = find_optional_unique_visible(
                page, stop_selectors, name="stop_button"
            )
            saw_stop_visible = saw_stop_visible or stop_button is not None
        except BrowserNotifyError as e:
            logger.info("Browser callback ACK secondary observation failed: %s", e)

        if time.monotonic() >= deadline:
            logger.warning(
                "Browser callback DELIVERY_ACK_TIMEOUT/DELIVERY_UNKNOWN: %s "
                "user_message_selector_before=%r user_message_selector_after=%r "
                "user_message_count_before=%s user_message_count_after=%s "
                "new_user_message_count=%s marker_match_seen=%s "
                "job_id_match_seen=%s combined_identity_match_seen=%s "
                "composer_empty_seen=%s stop_seen=%s timeout_seconds=%s",
                callback_log_identity(message), selector_before, selector_after,
                user_message_count_before,
                user_message_count_after, new_user_message_count,
                marker_match_seen, job_id_match_seen,
                combined_identity_match_seen, saw_composer_empty,
                saw_stop_visible, DELIVERY_ACK_TIMEOUT_SECONDS,
            )
            raise BrowserNotifyError("DELIVERY_UNKNOWN: DELIVERY_ACK_TIMEOUT")

        page.wait_for_timeout(STATE_POLL_INTERVAL_MS)


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

            user_messages_before = user_message_snapshot(
                page, dom["user_message"]["selectors"]
            )

            filled = False
            click_attempted = False
            try:
                composer.fill(message)
                filled = True

                send_button = wait_for_send_button(
                    page,
                    dom["send_button"]["selectors"],
                )
                if not send_button.is_enabled():
                    raise BrowserNotifyError("SEND_BUTTON_DISABLED")

                click_attempted = True
                try:
                    send_button.click()
                except Exception as e:
                    raise BrowserNotifyError(
                        "DELIVERY_UNKNOWN: CLICK_RESULT_UNKNOWN"
                    ) from e
                logger.info(
                    "Browser callback CLICK_SUCCEEDED: %s",
                    callback_log_identity(message),
                )
            except Exception:
                if filled and not click_attempted:
                    cleanup_inserted_callback(
                        page,
                        dom["composer"]["selectors"],
                        message,
                    )
                raise

            wait_for_delivery_ack(
                page,
                composer_selectors=dom["composer"]["selectors"],
                stop_selectors=dom["stop_button"]["selectors"],
                user_message_selectors=dom["user_message"]["selectors"],
                message=message,
                user_messages_before=user_messages_before,
            )

    except BrowserNotifyError:
        raise

    except Exception as e:
        raise BrowserNotifyError(
            f"BROWSER_CALLBACK_FAILED: {e}"
        ) from e
