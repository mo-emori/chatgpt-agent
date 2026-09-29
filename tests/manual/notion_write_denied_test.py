import os

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


# --------------------------------------------------
# Attempt a harmless page update.
#
# archived=False is already the current state.
# Even though the requested value is unchanged,
# Notion must reject the PATCH because this token
# has no update-content capability.
# --------------------------------------------------

url = (
    "https://api.notion.com/v1/pages/"
    f"{PAGE_ID}"
)

response = requests.patch(
    url,
    headers=headers,
    json={
        "archived": False
    },
    timeout=10,
)


print(
    "HTTP:",
    response.status_code,
)

print(
    "BODY:",
    response.text,
)


if response.status_code == 200:
    raise RuntimeError(
        "SECURITY FAILURE: "
        "read-only token accepted a write"
    )


if response.status_code in {
    401,
    403,
}:
    print()
    print(
        "READ-ONLY ENFORCEMENT: PASS"
    )
    raise SystemExit(0)


raise RuntimeError(
    "Write was rejected, but with an "
    f"unexpected status: {response.status_code}"
)