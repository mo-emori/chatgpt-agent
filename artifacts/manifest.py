import json
from dataclasses import dataclass


START_MARKER = "<AGENT_RESULT>"
END_MARKER = "</AGENT_RESULT>"

MANIFEST_INSTRUCTION = """
実行終了時、通常の最終報告の末尾に、
必ず次のmachine-readable blockを出力してください。

<AGENT_RESULT>
{
  "summary": "実行結果の短い要約",
  "artifacts": [
    "workspaceからの相対パス"
  ]
}
</AGENT_RESULT>

成果物がない場合は artifacts を空配列にしてください。
このblockは必ず最後に1回だけ出力してください。
""".strip() + """


Contract: artifacts is only for files intended for external delivery. Do not
list repo-canonical docs, evidence, context, baselines, or .agent declarations;
those changes are reported through normal git/change/evidence fields.
""".rstrip("\n")

# Contract: ``artifacts`` contains only files intended for external delivery.
# Repo-canonical docs, evidence, context, baselines, and declarations are
# reported through Worker-observed git/change evidence, not this field.

class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class AgentResult:
    summary: str
    artifacts: tuple[str, ...]


def parse_agent_result(text: str) -> AgentResult:
    start = text.rfind(START_MARKER)

    if start == -1:
        raise ManifestError(
            "AGENT_RESULT start marker not found"
        )

    start += len(START_MARKER)

    end = text.find(
        END_MARKER,
        start,
    )

    if end == -1:
        raise ManifestError(
            "AGENT_RESULT end marker not found"
        )

    raw = text[start:end].strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ManifestError(
            f"Invalid AGENT_RESULT JSON: {e}"
        ) from e

    if not isinstance(data, dict):
        raise ManifestError(
            "AGENT_RESULT must be an object"
        )

    summary = data.get("summary")
    artifacts = data.get("artifacts")

    if not isinstance(summary, str):
        raise ManifestError(
            "summary must be a string"
        )

    if not isinstance(artifacts, list):
        raise ManifestError(
            "artifacts must be an array"
        )

    if not all(
        isinstance(path, str)
        and path.strip()
        for path in artifacts
    ):
        raise ManifestError(
            "artifact paths must be non-empty strings"
        )

    return AgentResult(
        summary=summary.strip(),
        artifacts=tuple(artifacts),
    )


def append_manifest_instruction(
    prompt: str,
) -> str:
    return (
        prompt.rstrip()
        + "\n\n"
        + MANIFEST_INSTRUCTION
    )
