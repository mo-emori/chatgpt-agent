import json
from playwright.sync_api import sync_playwright

from config import BROWSER_CONFIG


TARGET_URL = "https://chatgpt.com/c/6ab6c87f-0ca0-83e8-a2f7-ab969d4d8f65"


def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(
            BROWSER_CONFIG["cdp_url"]
        )

        target = TARGET_URL.rstrip("/")
        pages = [
            page
            for context in browser.contexts
            for page in context.pages
            if page.url.rstrip("/") == target
        ]

        if len(pages) != 1:
            raise RuntimeError(
                f"target pages: {len(pages)}"
            )

        page = pages[0]

        buttons = page.locator("form button")

        print(f"form buttons: {buttons.count()}")
        print()

        for i in range(buttons.count()):
            button = buttons.nth(i)

            data = button.evaluate("""
                el => ({
                    tag: el.tagName,
                    type: el.getAttribute("type"),
                    ariaLabel: el.getAttribute("aria-label"),
                    dataTestId: el.getAttribute("data-testid"),
                    title: el.getAttribute("title"),
                    disabled: el.disabled,
                    text: el.innerText,
                    html: el.outerHTML
                })
            """)

            print(f"===== BUTTON {i} =====")
            print("visible:", button.is_visible())
            print(json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ))
            print()

        # compose 出力
        composer = page.locator(
            "form [contenteditable='true'][role='textbox']"
        ).first

        print("innerText repr:",
            repr(composer.evaluate("el => el.innerText")))

        print("textContent repr:",
            repr(composer.evaluate("el => el.textContent")))

        print("innerHTML:",
            composer.evaluate("el => el.innerHTML"))


if __name__ == "__main__":
    main()