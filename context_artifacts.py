"""Conservative, standalone cleanup for Context Harness generated artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath


LATEST_NAMES = frozenset({
    "evidence-index.json", "evidence-index-report.json",
    "job-context.json", "job-context-report.json",
    "job-context-pre-actor.json", "job-context-report-pre-actor.json",
    "job-context-post-actor-validation.json",
    "job-context-report-post-actor-validation.json",
    "materialized-context.json", "materialized-context-report.json",
    "materialized-context-pre-actor.json",
    "materialized-context-report-pre-actor.json",
    "materialized-context-post-actor-validation.json",
    "materialized-context-report-post-actor-validation.json",
})


class CleanupError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _safe_relative(value: str) -> Path:
    posix = PurePosixPath(value.replace("\\", "/"))
    if posix.is_absolute() or not posix.parts or any(
            part in ("", ".", "..") for part in posix.parts):
        raise CleanupError(f"unsafe generated_root: {value!r}")
    return Path(*posix.parts)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _entry(workspace: Path, path: Path, category: str, action: str,
           reason: str) -> dict:
    item = {"path": path.relative_to(workspace).as_posix(),
            "category": category, "action": action, "reason": reason,
            "bytes": None}
    if path.is_file() and not path.is_symlink():
        try:
            item["bytes"] = path.stat().st_size
        except OSError:
            pass
    return item


def build_plan(workspace: str | Path) -> dict:
    workspace = Path(workspace).absolute()
    declaration_path = workspace / ".agent" / "context.json"
    if not declaration_path.is_file() or declaration_path.is_symlink():
        raise CleanupError("regular .agent/context.json is required")
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    generated_rel = _safe_relative(
        declaration.get("generated_root", "validation/context"))
    generated = workspace / generated_rel
    workspace_resolved = workspace.absolute()

    # The declaration is the sole allowlist source.  Reject indirection in every
    # existing path component before resolving it.
    cursor = workspace
    for part in generated_rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise CleanupError(f"symlink in generated_root: {cursor}")
    if generated.exists() and not _is_within(
            generated.absolute(), workspace_resolved):
        raise CleanupError("generated_root resolves outside workspace")

    entries = []
    if generated.is_dir():
        for capability in sorted(generated.iterdir(), key=lambda p: p.name):
            if capability.is_symlink():
                entries.append(_entry(workspace, capability, "unsafe_path", "reject",
                                      "symlink capability entry"))
                continue
            if not capability.is_dir():
                entries.append(_entry(workspace, capability, "ambiguous", "retain",
                                      "unknown generated-root entry"))
                continue
            if not _is_within(capability.absolute(), generated.absolute()):
                entries.append(_entry(workspace, capability, "unsafe_path", "reject",
                                      "resolved path outside allowlisted root"))
                continue
            for path in sorted(capability.iterdir(), key=lambda p: p.name):
                if path.is_symlink():
                    entries.append(_entry(workspace, path, "unsafe_path", "reject",
                                          "symlink entry"))
                elif path.is_dir():
                    entries.append(_entry(workspace, path, "job_audit_evidence", "retain",
                                          "job-scoped evidence retained by default"))
                elif path.name in LATEST_NAMES:
                    entries.append(_entry(workspace, path, "replaceable_latest", "delete",
                                          "capability latest snapshot is reproducible"))
                elif path.name.endswith((".tmp", ".temp")):
                    entries.append(_entry(workspace, path, "ephemeral_temp", "delete",
                                          "capability-root temporary file"))
                else:
                    entries.append(_entry(workspace, path, "ambiguous", "retain",
                                          "unrecognized artifact retained"))

    body = {"schema_version": 1, "workspace": str(workspace),
            "allowlisted_roots": [generated_rel.as_posix()], "entries": entries}
    body["plan_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def apply_plan(plan: dict) -> dict:
    material = {key: value for key, value in plan.items() if key != "plan_sha256"}
    expected = hashlib.sha256(_canonical(material)).hexdigest()
    if plan.get("plan_sha256") != expected:
        raise CleanupError("plan validation failed")
    workspace = Path(plan["workspace"])
    allowed = [(workspace / _safe_relative(value)).absolute()
               for value in plan["allowlisted_roots"]]
    results = []
    for item in plan["entries"]:
        result = dict(item)
        if item["action"] != "delete":
            result["result"] = "skipped"
            results.append(result)
            continue
        path = workspace / _safe_relative(item["path"])
        try:
            if path.is_symlink():
                raise CleanupError("path became a symlink after planning")
            resolved = path.absolute()
            if not any(_is_within(resolved, root) and resolved != root for root in allowed):
                raise CleanupError("path is outside allowlisted roots")
            if not path.is_file():
                raise CleanupError("planned path is not a regular file")
            if path.stat().st_size != item.get("bytes"):
                raise CleanupError("planned file changed after planning")
            path.unlink()
            result["result"] = "deleted"
        except FileNotFoundError:
            result["result"] = "skipped"
            result["detail"] = "missing after planning"
        except Exception as exc:
            result["result"] = "error"
            result["detail"] = str(exc)
        results.append(result)
    return {"schema_version": 1, "mode": "apply", "plan_sha256": expected,
            "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--workspace", default=".")
    cleanup.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args.workspace)
        output = {"schema_version": 1,
                  "mode": "apply" if args.apply else "dry-run", "plan": plan}
        if args.apply:
            output["result"] = apply_plan(plan)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        if args.apply and any(x["result"] == "error"
                              for x in output["result"]["results"]):
            return 1
        return 0
    except Exception as exc:
        print(json.dumps({"schema_version": 1, "status": "error",
                          "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
