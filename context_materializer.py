"""Phase 2C deterministic bounded context materializer.

This is a comparison-only data product.  It is deliberately not actor input.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from job_context import canonical, safe_file, safe_relative, sha256


SCHEMA = "context-harness-materialized-context"
SCHEMA_VERSION = 1
BUILDER_VERSION = "context-harness-phase2c-1"
MODE = "COMPARISON_ONLY"


def _verified_json(root: Path, rel: str, expected: str | None = None) -> tuple[dict, bytes]:
    raw = safe_file(root, rel).read_bytes()
    if expected is not None and sha256(raw) != expected:
        raise ValueError(f"hash mismatch: {rel}")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {rel}")
    return value, raw


def _protected(rel: str, boundaries: dict) -> bool:
    path = safe_relative(rel)
    for prefix in boundaries.get("protected_paths", []) + boundaries.get("forbidden_paths", []):
        prefix = safe_relative(prefix)
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def _evidence_projection(entry: dict) -> dict:
    """Canonical structured evidence only; prose/raw local references never enter."""
    keys = ("evidence_id", "evidence_type", "job_id", "actor", "mode", "workspace",
            "capability", "job_status", "failure_class", "artifact_status",
            "baseline_head", "head", "canonical_head", "instruction_sha256",
            "actor_execution_status", "review_verdict", "findings", "predecessor",
            "successor", "review_of", "trust", "trust_limitation", "quality",
            "provenance")
    return {key: entry.get(key) for key in keys}


def build(root: str | Path, *, workspace: str, capability: str, job_context: dict,
          job_context_path: str, evidence_index: dict, evidence_index_path: str) -> tuple[dict, dict]:
    root = Path(root).resolve()
    diagnostics, omitted, items = [], [], []
    expansions = list(job_context.get("expansion_requirements", []))
    status = "READY_BOUNDED"
    try:
        stored_job, job_raw = _verified_json(root, job_context_path)
        stored_index, index_raw = _verified_json(root, evidence_index_path)
        if stored_job != job_context or stored_index != evidence_index:
            raise ValueError("stale substitution between in-memory and persisted input")
        material = dict(job_context)
        claimed = material.pop("manifest_sha256", None)
        package_claimed = material.pop("package_sha256", None)
        if claimed != package_claimed or sha256(canonical(material)) != claimed:
            raise ValueError("Job Context internal hash mismatch")
        if job_context.get("selection_status") != "READY_BOUNDED":
            raise ValueError("Job Context is not READY_BOUNDED")
        if job_context.get("workspace") != workspace or job_context.get("capability") != capability:
            raise ValueError("Job Context workspace/capability mismatch")
        if sha256(index_raw) != job_context.get("evidence_index_sha256"):
            raise ValueError("Evidence Index chain mismatch")
        budget = job_context.get("size", {}).get("configured_max_text_bytes")
        if budget is None:
            status = "NEEDS_EXPANSION"
            diagnostics.append({"code": "MISSING_EXPLICIT_BYTE_BUDGET",
                                "detail": "Phase 2B/default contract supplies no max_text_bytes"})
        elif not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
            raise ValueError("invalid byte budget")
        entries = {x.get("evidence_id"): x for x in evidence_index.get("entries", [])}
        candidates = []
        for ref in job_context.get("authoritative_source_refs", []):
            rel = safe_relative(ref["source_ref"])
            if _protected(rel, job_context.get("boundaries", {})):
                raise ValueError(f"selected source crosses protected/forbidden boundary: {rel}")
            raw = safe_file(root, rel).read_bytes()
            if sha256(raw) != ref.get("raw_sha256"):
                raise ValueError(f"hash mismatch: {rel}")
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                omitted.append({"source_ref": rel, "evidence_ref": None, "known_bytes": len(raw),
                                "reason": "UNSUPPORTED_BINARY_REQUIRED_SOURCE"})
                expansions.append({"source_ref": rel, "evidence_ref": None,
                    "reason": "UNSUPPORTED_BINARY_REQUIRED_SOURCE", "reason_code": "UNSUPPORTED_BINARY_REQUIRED_SOURCE",
                    "authority_class": "authoritative", "quality": None,
                    "requirement": "REQUIRED_BEFORE_REVIEW", "required_before_package_first_execution": True,
                    "origin": "MATERIALIZER", "suggested_scope": [rel]})
                status = "NEEDS_EXPANSION"
                continue
            candidates.append({"source_ref": rel, "evidence_ref": None,
                "authority_class": ref.get("authority") or "authoritative", "trust_class": ["DECLARED_AUTHORITY"],
                "quality": None, "source_sha256": sha256(raw), "content_sha256": sha256(raw),
                "content_encoding": "base64", "content_type": "text/plain; charset=utf-8",
                "byte_count": len(raw), "content": base64.b64encode(raw).decode("ascii")})
        for ref in job_context.get("evidence_refs", []):
            entry = entries.get(ref.get("evidence_id"))
            if entry is None or entry.get("evidence_path") != ref.get("evidence_path"):
                raise ValueError(f"selected evidence missing from index: {ref.get('evidence_id')}")
            for fact in entry.get("source_files", []):
                raw = safe_file(root, fact["path"]).read_bytes()
                if sha256(raw) != fact["sha256"]:
                    raise ValueError(f"evidence dependency hash mismatch: {fact['path']}")
            content = canonical(_evidence_projection(entry))
            candidates.append({"source_ref": entry["evidence_path"], "evidence_ref": entry["evidence_id"],
                "authority_class": "evidence", "trust_class": entry.get("trust", []),
                "quality": entry.get("quality"), "source_sha256": entry.get("manifest_sha256"),
                "content_sha256": sha256(content), "content_encoding": "utf-8-json",
                "content_type": "application/json", "byte_count": len(content),
                "content": content.decode("utf-8")})
        total = sum(x["byte_count"] for x in candidates)
        if budget is not None and total > budget:
            status = "NEEDS_EXPANSION"
            diagnostics.append({"code": "REQUIRED_ITEMS_EXCEED_BUDGET", "required_bytes": total,
                                "configured_max_text_bytes": budget})
            expansions.append({"source_ref": None, "evidence_ref": None,
                "reason": "REQUIRED_ITEMS_EXCEED_BUDGET", "reason_code": "REQUIRED_ITEMS_EXCEED_BUDGET",
                "authority_class": "mixed", "quality": None, "requirement": "REQUIRED_BEFORE_REVIEW",
                "required_before_package_first_execution": True, "origin": "MATERIALIZER", "suggested_scope": []})
        else:
            items = candidates
        for ref in job_context.get("optional_evidence_candidates", []):
            entry = entries.get(ref.get("evidence_id"), {})
            omitted.append({"source_ref": ref.get("evidence_path"), "evidence_ref": ref.get("evidence_id"),
                            "known_bytes": sum(f.get("size", 0) for f in entry.get("source_files", [])) or None,
                            "reason": "OPTIONAL_BOUNDED_CANDIDATE_NOT_PROMOTED"})
        for ref in job_context.get("omitted_irrelevant_evidence", []):
            omitted.append({"source_ref": ref.get("evidence_path"), "evidence_ref": ref.get("evidence_id"),
                            "known_bytes": None, "reason": "IRRELEVANT_PHASE_2B_OMISSION"})
    except Exception as exc:
        status, budget, job_raw, index_raw = "UNVERIFIABLE", None, b"", b""
        diagnostics.append({"code": "MATERIALIZATION_INPUT_UNVERIFIABLE", "detail": str(exc)[:1000]})
        items = []
    counts = {}
    for item in items:
        key = f"{item['authority_class']}:{item.get('quality') or 'NA'}"
        counts[key] = counts.get(key, 0) + 1
    body = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "builder_version": BUILDER_VERSION,
        "mode": MODE, "workspace": workspace, "capability": capability,
        "target_job": job_context.get("job", {}),
        "source_job_context": {"path": safe_relative(job_context_path), "sha256": sha256(job_raw) if job_raw else None},
        "source_context_manifest_sha256": job_context.get("source_context_manifest_sha256"),
        "evidence_index_sha256": sha256(index_raw) if index_raw else job_context.get("evidence_index_sha256"),
        "materialization_status": status, "materialized_items": items,
        "omitted_or_unsupported_items": sorted(omitted, key=lambda x: canonical(x)),
        "unresolved_items": job_context.get("unresolved_items", []),
        "expansion_requirements": sorted(expansions, key=lambda x: canonical(x)),
        "total_bytes": sum(x["byte_count"] for x in items), "configured_max_text_bytes": budget,
        "measurement": {"selected_source_bytes": sum(x["byte_count"] for x in items),
            "materialized_payload_bytes": sum(x["byte_count"] for x in items),
            "omitted_bytes_known": sum(x.get("known_bytes") or 0 for x in omitted),
            "materialized_item_count": len(items), "omitted_item_count": len(omitted),
            "item_counts_by_authority_quality": dict(sorted(counts.items()))},
        "diagnostics": diagnostics}
    identity = sha256(canonical(body))
    result = dict(body); result["materialized_context_sha256"] = identity
    report = {"mode": MODE, "status": status, "schema_version": SCHEMA_VERSION,
              "sha256": sha256(canonical(result)), "materialized_context_sha256": identity,
              "source_context_sha256": body["source_context_manifest_sha256"],
              "evidence_index_sha256": body["evidence_index_sha256"],
              "item_count": len(items), "bytes": len(canonical(result)),
              "payload_bytes": body["total_bytes"], "diagnostics": diagnostics}
    return result, report


def generate(root: str | Path, *, generated_root: str, **kwargs) -> dict:
    root = Path(root).resolve()
    destination = root / safe_relative(generated_root) / kwargs["capability"]
    destination.mkdir(parents=True, exist_ok=True)
    result, report = build(root, **kwargs)
    context_path = destination / "materialized-context.json"
    report_path = destination / "materialized-context-report.json"
    context_path.write_bytes(canonical(result))
    report["materialized_context_path"] = context_path.relative_to(root).as_posix()
    report["report_path"] = report_path.relative_to(root).as_posix()
    report_path.write_bytes(canonical(report))
    return report
