import os
import hashlib
import requests
from dotenv import load_dotenv


PAGE_ID = (
    "3e930bde-8a72-806b-b974-e4419233781a"
)

NOTION_VERSION = "2025-09-03"


load_dotenv()

token = os.getenv(
    "NOTION_WORKER_TOKEN"
)

if not token:
    raise RuntimeError(
        "NOTION_WORKER_TOKEN is not set"
    )


headers = {
    "Authorization": f"Bearer {token}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


url = (
    "https://api.notion.com/v1/pages/"
    f"{PAGE_ID}"
)

response = requests.get(
    url,
    headers=headers,
    timeout=10,
)


print(
    "HTTP:",
    response.status_code,
)

if response.status_code != 200:
    print(response.text)
    raise SystemExit(1)


page = response.json()

print(
    "PAGE OK"
)

print(
    "page_id:",
    page.get("id"),
)

print(
    "object:",
    page.get("object"),
)

print(
    "archived:",
    page.get("archived"),
)

blocks_url = (
    "https://api.notion.com/v1/blocks/"
    f"{PAGE_ID}/children"
)

response = requests.get(
    blocks_url,
    headers=headers,
    timeout=10,
)

print()
print(
    "BLOCKS HTTP:",
    response.status_code,
)

if response.status_code != 200:
    print(response.text)
    raise SystemExit(1)


data = response.json()
blocks = data.get(
    "results",
    [],
)

print(
    "BLOCK COUNT:",
    len(blocks),
)


for index, block in enumerate(blocks):
    block_type = block.get(
        "type"
    )

    print(
        f"{index}: {block_type}"
    )

    if block_type != "code":
        continue

    code = block.get(
        "code",
        {}
    )

    rich_text = code.get(
        "rich_text",
        []
    )

    text = "".join(
        item.get(
            "plain_text",
            ""
        )
        for item in rich_text
    )

    instruction_bytes = text.encode(
        "utf-8"
    )

    instruction_sha256 = hashlib.sha256(
        instruction_bytes
    ).hexdigest()

    print(
        "  UTF-8 bytes:",
        len(instruction_bytes)
    )

    print(
        "  SHA-256:",
        instruction_sha256
    )

    assert (
        instruction_bytes.decode("utf-8")
        == text
    )

    print(
        "  language:",
        code.get("language")
    )

    print(
        "  rich_text parts:",
        len(rich_text)
    )

    print(
        "----- CODE START -----"
    )

    print(text)

    print(
        "----- CODE END -----"
    )