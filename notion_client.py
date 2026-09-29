import hashlib
import os

import requests


NOTION_VERSION = "2025-09-03"


class NotionInstructionError(Exception):
    pass


def _headers():
    token = os.getenv(
        "NOTION_WORKER_TOKEN"
    )

    if not token:
        raise NotionInstructionError(
            "NOTION_WORKER_TOKEN_MISSING"
        )

    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def fetch_instruction(
    page_id: str,
):
    headers = _headers()

    # -------------------------
    # Page existence / access
    # -------------------------

    page_url = (
        "https://api.notion.com/v1/pages/"
        f"{page_id}"
    )

    try:
        response = requests.get(
            page_url,
            headers=headers,
            timeout=10,
        )
    except requests.RequestException as e:
        raise NotionInstructionError(
            "NOTION_UNAVAILABLE"
        ) from e

    if response.status_code == 404:
        raise NotionInstructionError(
            "INSTRUCTION_NOT_FOUND"
        )

    if response.status_code in {
        401,
        403,
    }:
        raise NotionInstructionError(
            "INSTRUCTION_PERMISSION_DENIED"
        )

    if response.status_code != 200:
        raise NotionInstructionError(
            "NOTION_UNAVAILABLE"
        )

    # -------------------------
    # Direct child blocks
    # -------------------------

    blocks_url = (
        "https://api.notion.com/v1/blocks/"
        f"{page_id}/children"
    )

    try:
        response = requests.get(
            blocks_url,
            headers=headers,
            timeout=10,
        )
    except requests.RequestException as e:
        raise NotionInstructionError(
            "NOTION_UNAVAILABLE"
        ) from e

    if response.status_code == 404:
        raise NotionInstructionError(
            "INSTRUCTION_NOT_FOUND"
        )

    if response.status_code in {
        401,
        403,
    }:
        raise NotionInstructionError(
            "INSTRUCTION_PERMISSION_DENIED"
        )

    if response.status_code != 200:
        raise NotionInstructionError(
            "NOTION_UNAVAILABLE"
        )

    blocks = response.json().get(
        "results",
        [],
    )

    code_blocks = [
        block
        for block in blocks
        if block.get("type") == "code"
    ]

    if not code_blocks:
        raise NotionInstructionError(
            "INSTRUCTION_BLOCK_MISSING"
        )

    if len(code_blocks) != 1:
        raise NotionInstructionError(
            "INSTRUCTION_BLOCK_AMBIGUOUS"
        )

    code = code_blocks[0].get(
        "code",
        {},
    )

    if code.get("language") not in {
        "plain text",
        "plain_text",
    }:
        raise NotionInstructionError(
            "INSTRUCTION_BLOCK_INVALID_LANGUAGE"
        )

    rich_text = code.get(
        "rich_text",
        [],
    )

    text = "".join(
        part.get(
            "plain_text",
            "",
        )
        for part in rich_text
    )

    if not text.strip():
        raise NotionInstructionError(
            "INSTRUCTION_EMPTY"
        )

    # No normalization.
    instruction_bytes = text.encode(
        "utf-8"
    )

    instruction_sha256 = (
        hashlib.sha256(
            instruction_bytes
        ).hexdigest()
    )

    return {
        "text": text,
        "bytes": instruction_bytes,
        "sha256": instruction_sha256,
        "page_id": page_id,
    }