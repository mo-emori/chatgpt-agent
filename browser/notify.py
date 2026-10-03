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

DELIVERY_CONFIRMED = "DELIVERY_CONFIRMED"
SUBMITTED_ACK_UNVERIFIED = "SUBMITTED_ACK_UNVERIFIED"


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
    snapshot = composer_snapshot(composer)
    if snapshot["tag_name"] in {"INPUT", "TEXTAREA"}:
        return snapshot["value"] or ""
    return snapshot["plain_text"] or ""


def composer_kind(snapshot):
    if snapshot["tag_name"] in {"INPUT", "TEXTAREA"}:
        return "form_control"
    if (
        snapshot["tag_name"] == "DIV"
        and (snapshot["contenteditable"] or "").lower() == "true"
    ):
        return "contenteditable"
    raise BrowserNotifyError("UNSUPPORTED_COMPOSER_ELEMENT")


def composer_snapshot(composer):
    data = composer.evaluate("""element => {
        const inlineText = node => {
            if (node.nodeType === Node.TEXT_NODE) return node.data;
            if (node.nodeType !== Node.ELEMENT_NODE) return '';
            if (node.tagName === 'BR') return '\\n';
            return Array.from(node.childNodes).map(inlineText).join('');
        };
        const children = Array.from(element.childNodes);
        const isBlock = node => node.nodeType === Node.ELEMENT_NODE &&
            (node.tagName === 'P' || node.tagName === 'DIV');
        const blockText = node => {
            if (node.childNodes.length === 1 && node.firstChild.nodeType === 1 &&
                    node.firstChild.tagName === 'BR') return '';
            let value = inlineText(node);
            if (node.lastChild && node.lastChild.nodeType === 1 &&
                    node.lastChild.tagName === 'BR') value = value.slice(0, -1);
            return value;
        };
        const allBlocks = children.length > 0 && children.every(isBlock);
        const range = document.createRange();
        range.selectNodeContents(element);
        const describe = node => ({
            node_type: node.nodeType,
            tag_name: node.nodeType === 1 ? node.tagName : null,
            text_length: (node.textContent || '').length,
            ...(node.nodeType === 1 ? {
                child_count: node.childNodes.length,
                child_tags: Array.from(node.childNodes).slice(0, 12).map(child =>
                    child.nodeType === 3 ? '#text' : child.tagName),
            } : {}),
        });
        return ({
        tagName: element.tagName,
        contenteditable: element.getAttribute('contenteditable'),
        hasValue: 'value' in element,
        value: typeof element.value === 'string' ? element.value : null,
        innerText: element.innerText,
        textContent: element.textContent,
        rangeText: range.toString(),
        plainText: allBlocks
            ? children.map(blockText).join('\\n')
            : children.map(inlineText).join(''),
        extractionMode: allBlocks ? 'direct-block-lines' : 'inline-tree',
        childNodes: children.slice(0, 64).map(describe),
        childNodeCount: children.length,
    }); }""")
    return {
        "tag_name": (data.get("tagName") or "").upper(),
        "contenteditable": data.get("contenteditable"),
        "has_value": bool(data.get("hasValue")),
        "value": data.get("value"),
        "inner_text": data.get("innerText") or "",
        "text_content": data.get("textContent") or "",
        "range_text": data.get("rangeText") or "",
        "plain_text": data.get("plainText") or "",
        "extraction_mode": data.get("extractionMode"),
        "child_nodes": data.get("childNodes") or [],
        "child_node_count": data.get("childNodeCount", 0),
    }


def canonicalize_composer_text(value):
    return value.replace("\r\n", "\n").replace("\r", "\n")


def composer_fill_matches(actual, expected, *, kind):
    if actual == expected:
        return True
    if kind == "form_control":
        return canonicalize_composer_text(actual) == canonicalize_composer_text(expected)
    return False


def insert_composer_text(page, composer, message, snapshot):
    kind = composer_kind(snapshot)
    if kind == "form_control":
        composer.fill(message)
    else:
        # Playwright fill() rewrites contenteditable markup and can make a
        # ProseMirror-style editor expose extra paragraph newlines.  CDP's
        # Input.insertText path is the supported, user-like plain-text input
        # primitive and leaves the editor responsible for its normal input
        # event handling.
        composer.focus()
        page.keyboard.insert_text(message)
    return kind


def clear_composer(composer, *, kind):
    if kind == "form_control":
        composer.fill("")
    else:
        composer.focus()
        composer.press("ControlOrMeta+A")
        composer.press("Backspace")


def _newline_counts(value):
    crlf = value.count("\r\n")
    return {
        "crlf": crlf,
        "lf": value.count("\n") - crlf,
        "cr": value.count("\r") - crlf,
    }


def _escaped_mismatch_snippet(value, index, radius=16):
    start = max(0, index - radius)
    end = min(len(value), index + radius)
    snippet = value[start:end].encode("unicode_escape").decode("ascii")
    return f"{start}:{end}:{snippet!r}"


def _candidate_diagnostics(value, expected):
    expected_normalized = canonicalize_composer_text(expected)
    actual_normalized = canonicalize_composer_text(value)
    mismatch_index = next(
        (
            index
            for index, (expected_char, actual_char) in enumerate(
                zip(expected_normalized, actual_normalized)
            )
            if expected_char != actual_char
        ),
        min(len(expected_normalized), len(actual_normalized)),
    )
    marker, job_id = callback_identity(expected)
    actual_lines = {line.strip() for line in actual_normalized.splitlines()}
    return {
        "length": len(value),
        "newline_counts": _newline_counts(value),
        "first_mismatch_index": mismatch_index,
        "matches": value == expected,
        "marker_present": marker in actual_lines,
        "job_id_present": f"job_id: {job_id}" in actual_lines,
    }


def composer_fill_diagnostics(snapshot, expected, actual):
    expected_lines = expected.split("\n")
    return {
        "tag_name": snapshot["tag_name"],
        "contenteditable": snapshot["contenteditable"],
        "has_value": snapshot["has_value"],
        "extraction_mode": snapshot["extraction_mode"],
        "child_node_count": snapshot["child_node_count"],
        "child_nodes": snapshot["child_nodes"],
        "expected_length": len(expected),
        "expected_line_count": len(expected_lines),
        "expected_blank_line_positions": [
            index for index, line in enumerate(expected_lines) if line == ""
        ],
        "expected_newlines": _newline_counts(expected),
        "plain_text": _candidate_diagnostics(actual, expected),
        "inner_text": _candidate_diagnostics(snapshot["inner_text"], expected),
        "text_content": _candidate_diagnostics(snapshot["text_content"], expected),
        "range_text": _candidate_diagnostics(snapshot["range_text"], expected),
    }


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
    submission_transition_seen = False
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
            return DELIVERY_CONFIRMED

        try:
            composer = find_optional_unique_visible(
                page, composer_selectors, name="composer"
            )
            composer_empty_now = (
                composer is not None and composer_is_empty(composer)
            )
            saw_composer_empty = saw_composer_empty or composer_empty_now
            stop_button = find_optional_unique_visible(
                page, stop_selectors, name="stop_button"
            )
            stop_visible_now = stop_button is not None
            saw_stop_visible = saw_stop_visible or stop_visible_now
            # The composer was verified empty, then populated by this invocation
            # immediately before the click.  Empty + generation after the click
            # is therefore an independent submission transition, even when the
            # UI exposes no queryable user-message node.
            submission_transition_seen = submission_transition_seen or (
                saw_composer_empty and saw_stop_visible
            )
        except BrowserNotifyError as e:
            logger.info("Browser callback ACK secondary observation failed: %s", e)

        if time.monotonic() >= deadline:
            if submission_transition_seen:
                logger.warning(
                    "Browser callback SUBMITTED_ACK_UNVERIFIED: %s "
                    "identity ACK unavailable; composer emptied and generation "
                    "started after click user_message_selector_before=%r "
                    "user_message_selector_after=%r",
                    callback_log_identity(message), selector_before, selector_after,
                )
                return SUBMITTED_ACK_UNVERIFIED
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


def cleanup_inserted_callback(
    page, composer_selectors, message, *, observed_readback=None,
    inserted_kind=None,
):
    try:
        composer = find_optional_unique_visible(
            page, composer_selectors, name="composer"
        )
        if composer is not None:
            snapshot = composer_snapshot(composer)
            kind = composer_kind(snapshot)
            current = composer_text(composer)
            invocation_text_unchanged = kind == inserted_kind and (
                composer_fill_matches(current, message, kind=kind)
                or (observed_readback is not None and current == observed_readback)
            )
            if invocation_text_unchanged:
                clear_composer(composer, kind=kind)
                if not composer_is_empty(composer):
                    logger.error("Browser callback COMPOSER_CLEANUP_NOT_VERIFIED")
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
            observed_readback = None
            inserted_kind = None
            try:
                initial_snapshot = composer_snapshot(composer)
                inserted_kind = insert_composer_text(
                    page, composer, message, initial_snapshot
                )
                filled = True
                snapshot = composer_snapshot(composer)
                actual = (
                    snapshot["value"] or ""
                    if snapshot["tag_name"] in {"INPUT", "TEXTAREA"}
                    else snapshot["plain_text"] or ""
                )
                observed_readback = actual
                if not composer_fill_matches(actual, message, kind=inserted_kind):
                    logger.error(
                        "Browser callback COMPOSER_FILL_NOT_VERIFIED: %s",
                        composer_fill_diagnostics(snapshot, message, actual),
                    )
                    raise BrowserNotifyError("COMPOSER_FILL_NOT_VERIFIED")
                if inserted_kind == "contenteditable":
                    logger.info(
                        "Browser callback COMPOSER_STRUCTURE_VERIFIED: %s",
                        composer_fill_diagnostics(snapshot, message, actual),
                    )

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
                        observed_readback=observed_readback,
                        inserted_kind=inserted_kind,
                    )
                raise

            return wait_for_delivery_ack(
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
