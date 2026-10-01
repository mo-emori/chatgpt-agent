import json


def parse_stream_json(raw):
    events = []
    final_text = None
    for order, line in enumerate(raw.splitlines()):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = item.get("type")
        if kind == "result":
            final_text = item.get("result") if isinstance(item.get("result"), str) else final_text
            events.append({"order": order, "kind": "result", "subtype": item.get("subtype"),
                           "is_error": item.get("is_error")})
        message = item.get("message")
        if isinstance(message, dict):
            for block in message.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    entry = {"order": order, "kind": "tool_use", "tool": block.get("name"),
                             "tool_use_id": block.get("id")}
                    command = block.get("input", {}).get("command") if isinstance(block.get("input"), dict) else None
                    if command is not None:
                        entry["command"] = command
                    events.append(entry)
                elif block.get("type") == "tool_result":
                    events.append({"order": order, "kind": "tool_result",
                                   "tool_use_id": block.get("tool_use_id"),
                                   "is_error": block.get("is_error", False),
                                   "content": block.get("content")})
    return final_text, events
