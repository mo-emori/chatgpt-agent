"""Phase 2C deterministic bounded context materializer.

This is a comparison-only data product.  It is deliberately not actor input.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from job_context import canonical, safe_file, safe_relative, sha256
from section_slicing import COMPLETE, merge_selected


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
    budget_contract = job_context.get("budget_contract", {})
    declared_budget = budget_contract.get("declared_max_text_bytes")
    requested_budget = budget_contract.get("requested_max_text_bytes")
    effective_budget = budget_contract.get("effective_max_text_bytes")
    budget_source = budget_contract.get("budget_source")
    budget_validation_status = budget_contract.get("validation_status")
    budget_reason = budget_contract.get("reason")
    required_payload_bytes = 0
    over_budget_bytes = 0
    whole_file_bytes = selected_authority_bytes = 0
    selected_section_ids = []
    section_coverage = []
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
        entries = {x.get("evidence_id"): x for x in evidence_index.get("entries", [])}
        candidates = []
        authority_refs = sorted(job_context.get("authoritative_source_refs", []),
            key=lambda x: (x.get("source_declaration_order", 10**9), x.get("source_ref", "")))
        for ref in authority_refs:
            rel = safe_relative(ref["source_ref"])
            if _protected(rel, job_context.get("boundaries", {})):
                raise ValueError(f"selected source crosses protected/forbidden boundary: {rel}")
            raw = safe_file(root, rel).read_bytes()
            whole_file_bytes += len(raw)
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
            if ref.get("section_coverage_status") == COMPLETE:
                verified = []
                for section in ref.get("selected_sections", []):
                    start, end = section.get("start_byte"), section.get("end_byte")
                    if (not isinstance(start, int) or not isinstance(end, int) or
                            start < 0 or end < start or end > len(raw)):
                        raise ValueError(f"SECTION_PROVENANCE_MISMATCH: {rel}")
                    payload = raw[start:end]
                    if (sha256(payload) != section.get("slice_sha256") or
                            base64.b64encode(payload).decode("ascii") != section.get("slice_payload")):
                        raise ValueError(f"SECTION_PROVENANCE_MISMATCH: {rel}#{section.get('section_id')}")
                    verified.append(section)
                for merged in merge_selected(verified, raw):
                    payload = raw[merged["start_byte"]:merged["end_byte"]]
                    selected_authority_bytes += len(payload)
                    selected_section_ids.extend(merged["section_ids"])
                    candidates.append({"source_ref": rel, "parent_source_path": rel,
                        "parent_source_sha256": sha256(raw), "evidence_ref": None,
                        "section_ids": merged["section_ids"], "boundaries": merged["boundaries"],
                        "resolved_start_byte": merged["start_byte"], "resolved_end_byte": merged["end_byte"],
                        "slice_sha256": merged["slice_sha256"], "context_items": merged["context_items"],
                        "selection_reasons": merged["selection_reasons"], "coverage_status": COMPLETE,
                        "authority_class": ref.get("authority") or "authoritative",
                        "trust_class": ["DECLARED_AUTHORITY"], "quality": None,
                        "source_sha256": sha256(raw), "content_sha256": sha256(payload),
                        "content_encoding": "base64", "content_type": "text/plain; charset=utf-8",
                        "byte_count": len(payload), "content": base64.b64encode(payload).decode("ascii")})
            else:
                selected_authority_bytes += len(raw)
                diagnostics.extend(ref.get("section_diagnostics", []))
                candidates.append({"source_ref": rel, "evidence_ref": None,
                    "authority_class": ref.get("authority") or "authoritative", "trust_class": ["DECLARED_AUTHORITY"],
                    "quality": None, "source_sha256": sha256(raw), "content_sha256": sha256(raw),
                    "content_encoding": "base64", "content_type": "text/plain; charset=utf-8",
                    "byte_count": len(raw), "content": base64.b64encode(raw).decode("ascii"),
                    **({"coverage_status": ref.get("section_coverage_status"),
                        "section_diagnostics": ref.get("section_diagnostics", [])}
                       if ref.get("section_coverage_status") else {})})
            section_coverage.append({"source_ref": rel,
                "status": ref.get("section_coverage_status", "WHOLE_FILE")})
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
        required_payload_bytes = sum(x["byte_count"] for x in candidates)
        if budget_reason:
            status = "NEEDS_EXPANSION"
            diagnostics.append({"code": budget_reason,
                "declared_max_text_bytes": declared_budget,
                "requested_max_text_bytes": requested_budget,
                "effective_max_text_bytes": effective_budget})
        elif (not isinstance(effective_budget, int) or isinstance(effective_budget, bool)
              or effective_budget <= 0):
            status = "NEEDS_EXPANSION"
            budget_reason = "MISSING_EXPLICIT_BYTE_BUDGET"
            diagnostics.append({"code": budget_reason})
        elif required_payload_bytes > effective_budget:
            status = "NEEDS_EXPANSION"
            budget_reason = "REQUIRED_CONTEXT_OVER_BUDGET"
            over_budget_bytes = required_payload_bytes - effective_budget
            oversized = [{"source_ref": x["source_ref"], "evidence_ref": x["evidence_ref"],
                          "byte_count": x["byte_count"]}
                         for x in candidates if x["byte_count"] > effective_budget]
            diagnostics.append({"code": budget_reason,
                "required_payload_bytes": required_payload_bytes,
                "effective_max_text_bytes": effective_budget,
                "over_budget_bytes": over_budget_bytes,
                "oversized_required_items": oversized})
            expansions.append({"source_ref": None, "evidence_ref": None,
                "reason": budget_reason, "reason_code": budget_reason,
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
        status, job_raw, index_raw = "UNVERIFIABLE", b"", b""
        code = "SECTION_PROVENANCE_MISMATCH" if "SECTION_PROVENANCE_MISMATCH" in str(exc) else "MATERIALIZATION_INPUT_UNVERIFIABLE"
        diagnostics.append({"code": code, "detail": str(exc)[:1000]})
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
        "total_bytes": sum(x["byte_count"] for x in items),
        "declared_max_text_bytes": declared_budget,
        "requested_max_text_bytes": requested_budget,
        "effective_max_text_bytes": effective_budget,
        "budget_source": budget_source,
        "budget_status": budget_validation_status or (
            "VALID" if budget_reason is None else "INVALID"),
        "budget_reason": budget_reason,
        "measurement": {"selected_source_bytes": sum(x["byte_count"] for x in items),
            "materialized_payload_bytes": sum(x["byte_count"] for x in items),
            "required_payload_bytes": required_payload_bytes,
            "over_budget_bytes": over_budget_bytes,
            "omitted_bytes_known": sum(x.get("known_bytes") or 0 for x in omitted),
            "materialized_item_count": len(items), "omitted_item_count": len(omitted),
            "item_counts_by_authority_quality": dict(sorted(counts.items())),
            "whole_file_bytes": whole_file_bytes,
            "selected_slice_bytes": selected_authority_bytes,
            "avoided_bytes": whole_file_bytes - selected_authority_bytes,
            "selected_section_count": len(selected_section_ids),
            "selected_section_ids": selected_section_ids,
            "section_coverage": section_coverage},
        "diagnostics": diagnostics}
    identity = sha256(canonical(body))
    result = dict(body); result["materialized_context_sha256"] = identity
    report = {"mode": MODE, "status": status, "schema_version": SCHEMA_VERSION,
              "sha256": sha256(canonical(result)), "materialized_context_sha256": identity,
              "source_context_sha256": body["source_context_manifest_sha256"],
              "evidence_index_sha256": body["evidence_index_sha256"],
              "item_count": len(items), "bytes": len(canonical(result)),
              "payload_bytes": body["total_bytes"],
              "declared_max_text_bytes": declared_budget,
              "requested_max_text_bytes": requested_budget,
              "effective_max_text_bytes": effective_budget,
              "budget_source": budget_source,
              "required_payload_bytes": required_payload_bytes,
              "over_budget_bytes": over_budget_bytes,
              "whole_file_bytes": body["measurement"]["whole_file_bytes"],
              "selected_slice_bytes": body["measurement"]["selected_slice_bytes"],
              "avoided_bytes": body["measurement"]["avoided_bytes"],
              "selected_section_count": body["measurement"]["selected_section_count"],
              "selected_section_ids": body["measurement"]["selected_section_ids"],
              "section_coverage": body["measurement"]["section_coverage"],
              "reason": budget_reason,
              "diagnostics": diagnostics}
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
