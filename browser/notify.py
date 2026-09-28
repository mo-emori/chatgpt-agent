import json
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
)

from config import BROWSER_CONFIG


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

            composer.fill(message)

            page.wait_for_timeout(
                300
            )

            send_button = (
                find_unique_visible(
                    page,
                    dom[
                        "send_button"
                    ]["selectors"],
                    name="send_button",
                )
            )

            send_button.click()

    except BrowserNotifyError:
        raise

    except Exception as e:
        raise BrowserNotifyError(
            f"BROWSER_CALLBACK_FAILED: {e}"
        ) from e