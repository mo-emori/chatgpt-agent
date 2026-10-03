"""Deterministic Phase-2B job-context candidate builder.

The output is comparison-only data.  It is never appended to an actor prompt and
it never interprets prose or changes approved semantics.
"""
from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path, PurePosixPath


SCHEMA = "context-harness-job-context"
SCHEMA_VERSION = 2
BUILDER_VERSION = "context-harness-phase2b-2"
MODE = "COMPARISON_ONLY"
STATUSES = ("READY_BOUNDED", "NEEDS_RECONCILIATION", "UNVERIFIABLE")
RELEVANCE = ("REQUIRED_RELEVANT", "BOUNDED_CANDIDATE", "IRRELEVANT")
EXPANSION_REQUIREMENTS = ("REQUIRED_BEFORE_REVIEW", "OPTIONAL_BOUNDED", "NONE")


def canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or ":" in value:
        raise ValueError(f"unsafe workspace-relative path: {value!r}")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe workspace-relative path: {value!r}")
    return path.as_posix()


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0)
                                     & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def safe_file(root: Path, rel: str) -> Path:
    rel = safe_relative(rel)
    current = root
    for part in Path(rel).parts:
        current /= part
        if current.exists() and _is_link(current):
            raise ValueError(f"symlink/reparse source rejected: {rel}")
    if not current.is_file():
        raise ValueError(f"required source missing: {rel}")
    return current


def _load_verified(root: Path, rel: str, expected_sha: str | None = None) -> tuple[dict, str]:
    raw = safe_file(root, rel).read_bytes()
    actual = sha256(raw)
    if expected_sha is not None and actual != expected_sha:
        raise ValueError(f"hash mismatch: {rel}")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {rel}")
    return value, actual


def _strings(value, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
        raise ValueError(f"{name} must be a list of non-empty strings")
    return sorted(set(value))


def structured_request(job) -> dict:
    """Read only an explicitly structured request; never inspect prompt prose."""
    ref = getattr(job, "instruction_ref", None) or {}
    request = ref.get("context_request", {}) if isinstance(ref, dict) else {}
    if not isinstance(request, dict):
        raise ValueError("instruction_ref.context_request must be an object")
    phase = request.get("phase")
    if phase is not None and (not isinstance(phase, str) or not phase):
        raise ValueError("context_request.phase must be a non-empty string")
    return {
        "phase": phase,
        "target_files": [safe_relative(x) for x in _strings(request.get("target_files"), "target_files")],
        "finding_ids": _strings(request.get("finding_ids"), "finding_ids"),
        "previous_finding_ids": _strings(request.get("previous_finding_ids"), "previous_finding_ids"),
        "context_items": _strings(request.get("context_items"), "context_items"),
        "evidence_ids": _strings(request.get("evidence_ids"), "evidence_ids"),
        "max_text_bytes": request.get("max_text_bytes"),
    }


def _reason(category: str, disposition: str, rule: str, count: int) -> dict:
    return {"category": category, "disposition": disposition, "rule": rule, "count": count}


def _classify_evidence(entry: dict, *, capability: str, target_job_id: str | None,
                       requested_findings: set[str], requested_evidence: set[str],
                       declared_evidence: dict[str, dict]) -> tuple[str, str]:
    """Classify from encoded edges only; prose and job names are deliberately ignored."""
    finding_ids = {x.get("finding_id") for x in (entry.get("findings") or [])}
    if entry.get("evidence_id") in requested_evidence:
        return "REQUIRED_RELEVANT", "EXPLICIT_EVIDENCE_ID"
    if requested_findings & finding_ids:
        return "REQUIRED_RELEVANT", "SELECTED_FINDING_ID"
    if target_job_id and target_job_id in {
            entry.get("review_of"), entry.get("predecessor"), entry.get("successor")}:
        return "REQUIRED_RELEVANT", "EXPLICIT_TARGET_JOB_EDGE"
    if entry.get("evidence_path") in declared_evidence:
        return "REQUIRED_RELEVANT", "DECLARED_EVIDENCE_SOURCE"
    entry_capability = entry.get("capability")
    if entry_capability is not None and entry_capability != capability:
        return "IRRELEVANT", "EXPLICIT_CAPABILITY_MISMATCH"
    required_types = {spec.get("evidence_type", spec.get("kind"))
                      for spec in declared_evidence.values() if spec.get("required") is True}
    if entry_capability == capability and entry.get("evidence_type") in required_types:
        return "REQUIRED_RELEVANT", "DECLARED_REQUIRED_CAPABILITY_EVIDENCE_TYPE"
    return "BOUNDED_CANDIDATE", "NO_REQUIRED_STRUCTURAL_EDGE"


def _expansion(*, evidence_ref=None, source_ref=None, reason: str,
               requirement: str, quality: str | None = None,
               authority_class: str = "evidence", origin: str = "JOB_CONTEXT",
               suggested_scope: list[str] | None = None) -> dict:
    return {"source_ref": source_ref, "evidence_ref": evidence_ref,
            "reason": reason, "reason_code": reason,
            "authority_class": authority_class, "quality": quality,
            "requirement": requirement,
            "required_before_package_first_execution": requirement == "REQUIRED_BEFORE_REVIEW",
            "origin": origin, "suggested_scope": sorted(set(suggested_scope or []))}


def _source_ref(spec: dict) -> str:
    if isinstance(spec.get("path"), str):
        return safe_relative(spec["path"])
    if isinstance(spec.get("glob"), str):
        return "glob:" + safe_relative(spec["glob"])
    raise ValueError("source requires exactly one path or glob")


def _selector_contract(config: dict) -> dict:
    """Validate and return the declaration-owned selector identity."""
    specs = config.get("sources", [])
    if not isinstance(specs, list):
        raise ValueError("sources must be a list")
    result, known = [], set()
    for spec in specs:
        if not isinstance(spec, dict) or not isinstance(spec.get("kind"), str):
            raise ValueError("invalid source declaration")
        if ("path" in spec) == ("glob" in spec):
            raise ValueError("source requires exactly one path or glob")
        ref = _source_ref(spec)
        if ref in known:
            raise ValueError(f"duplicate source reference: {ref}")
        known.add(ref)
        authority = spec.get("authority", "authoritative")
        if authority not in ("authoritative", "non_authority", "observed"):
            raise ValueError(f"invalid authority class: {ref}")
        if "always_required" in spec and not isinstance(spec["always_required"], bool):
            raise ValueError(f"always_required must be boolean: {ref}")
        fields = {}
        for field in ("context_items", "target_files", "depends_on"):
            values = spec.get(field, [])
            if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values):
                raise ValueError(f"{field} must be a list of non-empty strings: {ref}")
            fields[field] = sorted(set(safe_relative(x) for x in values)) if field == "target_files" else sorted(set(values))
        conditional = authority == "authoritative" and spec.get("always_required") is False
        if conditional and "glob" in spec:
            raise ValueError(f"conditional authority requires a stable path reference: {ref}")
        result.append({"source_ref": ref, "authority": authority,
                       "always_required": authority == "authoritative" and bool(spec.get("always_required", True)),
                       **fields})
    referenced = set()
    for item in result:
        normalized = []
        for dependency in item["depends_on"]:
            dep = ("glob:" + safe_relative(dependency[5:])
                   if dependency.startswith("glob:") else safe_relative(dependency))
            if dep not in known:
                raise ValueError(f"unknown source dependency: {item['source_ref']}->{dep}")
            normalized.append(dep)
            referenced.add(dep)
        item["depends_on"] = sorted(set(normalized))
    for item in result:
        if (item["authority"] == "authoritative" and not item["always_required"] and
                not item["target_files"] and not item["context_items"] and
                item["source_ref"] not in referenced):
            raise ValueError(f"conditional authority lacks a structural mapping: {item['source_ref']}")
    return {"version": 1, "sources": sorted(result, key=lambda x: x["source_ref"])}


def build(root: str | Path, *, workspace: str, capability: str, job,
          manifest: dict, manifest_path: str, delta: dict, delta_path: str,
          evidence_index: dict, evidence_index_path: str, declaration: dict) -> tuple[dict, dict]:
    root = Path(root).resolve()
    unresolved, reconciliation, expansion = [], [], []
    try:
        request = structured_request(job)
        stored_manifest, manifest_file_sha = _load_verified(root, manifest_path)
        stored_delta, delta_sha = _load_verified(root, delta_path)
        stored_index, index_sha = _load_verified(root, evidence_index_path)
        if stored_manifest != manifest or stored_delta != delta or stored_index != evidence_index:
            raise ValueError("stale substitution between in-memory and persisted input")
        expected_manifest_sha = manifest["lifecycle"]["manifest_sha256"]
        material = {"observed": manifest["observed"],
                    "approved_semantics": manifest["approved_semantics"]}
        if sha256(canonical(material)) != expected_manifest_sha:
            raise ValueError("context manifest content hash mismatch")
        if evidence_index.get("workspace") != workspace or evidence_index.get("capability") != capability:
            raise ValueError("evidence index workspace/capability mismatch")
        if evidence_index.get("status") != "READY":
            raise ValueError("evidence index is not READY")
        if delta.get("delta_status") not in ("NO_IMPACT", "CONTEXT_UPDATE",
                                               "POTENTIAL_AUTHORITY_CHANGE", "UNVERIFIABLE"):
            raise ValueError("unsupported delta status")
        # Revalidate every indexed canonical byte, including referenced package files.
        for fact in evidence_index.get("source_manifest", []):
            _load_verified(root, fact["path"], fact["sha256"])
        for entry in evidence_index.get("entries", []):
            for fact in entry.get("source_files", []):
                raw = safe_file(root, fact["path"]).read_bytes()
                if sha256(raw) != fact["sha256"]:
                    raise ValueError(f"evidence dependency hash mismatch: {fact['path']}")
    except Exception as exc:
        request = {"phase": None, "target_files": [], "finding_ids": [],
                   "previous_finding_ids": [], "context_items": [], "evidence_ids": [],
                   "max_text_bytes": None}
        unresolved.append({"code": "INPUT_UNVERIFIABLE", "detail": str(exc)[:1000]})
        stored_manifest, stored_delta, stored_index = manifest, delta, evidence_index
        manifest_file_sha = delta_sha = index_sha = None

    config = declaration.get("capabilities", {}).get(capability, {})
    try:
        selector_contract = _selector_contract(config)
    except Exception as exc:
        selector_contract = {"version": 1, "sources": []}
        unresolved.append({"code": "INVALID_DECLARATION_REFERENCE", "detail": str(exc)[:1000]})
    sources, omitted_sources, dependency_states = [], [], []
    changed = {x.get("source"): x for x in stored_delta.get("changed_sources", [])}
    target_files = set(request["target_files"])
    wanted_items = set(request["context_items"])
    observed_by_path = {x.get("path"): x for x in stored_manifest.get("observed", {}).get("sources", [])}
    protected = sorted(set(_strings(config.get("protected_paths"), "protected_paths") +
                           _strings(config.get("forbidden_paths"), "forbidden_paths")))
    specs = sorted(config.get("sources", []), key=lambda x: (x.get("path", x.get("glob", "")), x.get("kind", "")))
    spec_by_ref = {}
    for spec in specs:
        try:
            spec_by_ref[_source_ref(spec)] = spec
        except ValueError:
            continue
    conditional_refs = {ref for ref, spec in spec_by_ref.items()
                        if spec.get("authority", "authoritative") == "authoritative"
                        and spec.get("always_required") is False}
    selected_reasons: dict[str, set[str]] = {}
    for ref, spec in spec_by_ref.items():
        if (spec.get("authority", "authoritative") == "authoritative" and
                spec.get("always_required", True)):
            selected_reasons.setdefault(ref, set()).add("BASE_AUTHORITY")

    selector_present = bool(target_files or wanted_items)
    if conditional_refs and not selector_present:
        reconciliation.append({"code": "MISSING_STRUCTURED_REQUEST",
                               "conditional_source_refs": sorted(conditional_refs)})
        expansion.append(_expansion(reason="MISSING_STRUCTURED_REQUEST",
            requirement="REQUIRED_BEFORE_REVIEW", authority_class="authoritative",
            suggested_scope=sorted(conditional_refs)))
        for ref in conditional_refs:
            selected_reasons.setdefault(ref, set()).add("MISSING_STRUCTURED_REQUEST")
    else:
        mapped_targets, mapped_items = set(), set()
        for ref in sorted(spec_by_ref):
            spec = spec_by_ref[ref]
            target_hits = target_files.intersection(spec.get("target_files", []))
            item_hits = wanted_items.intersection(spec.get("context_items", []))
            if target_hits and ref in conditional_refs:
                selected_reasons.setdefault(ref, set()).add("TARGET_MATCH")
            if item_hits and ref in conditional_refs:
                selected_reasons.setdefault(ref, set()).add("CONTEXT_ITEM_MATCH")
            mapped_targets.update(target_hits)
            mapped_items.update(item_hits)
        unmapped_targets = target_files - mapped_targets
        unmapped_items = wanted_items - mapped_items
        if conditional_refs and (unmapped_targets or unmapped_items):
            reconciliation.append({"code": "AMBIGUOUS_SELECTOR_MAPPING",
                "unmapped_target_files": sorted(unmapped_targets),
                "unmapped_context_items": sorted(unmapped_items)})
            expansion.append(_expansion(reason="AMBIGUOUS_SELECTOR_MAPPING",
                requirement="REQUIRED_BEFORE_REVIEW", authority_class="authoritative",
                suggested_scope=sorted(conditional_refs)))
            for ref in conditional_refs:
                selected_reasons.setdefault(ref, set()).add("AMBIGUOUS_SELECTOR_MAPPING")

    # Authority changes are always required, independently of request selection.
    for ref, spec in spec_by_ref.items():
        if spec.get("authority", "authoritative") != "authoritative":
            continue
        declared = spec.get("path")
        if declared in changed:
            selected_reasons.setdefault(ref, set()).add("CHANGED_AUTHORITY")

    # Deterministic transitive closure. A visited set makes cycles terminate.
    pending = sorted(selected_reasons)
    visited = set()
    while pending:
        ref = pending.pop(0)
        if ref in visited:
            continue
        visited.add(ref)
        for dependency in sorted(spec_by_ref.get(ref, {}).get("depends_on", [])):
            dep = ("glob:" + safe_relative(dependency[5:])
                   if dependency.startswith("glob:") else safe_relative(dependency))
            if dep not in spec_by_ref:
                unresolved.append({"code": "INVALID_DECLARATION_REFERENCE",
                                   "source_ref": ref, "dependency_ref": dep})
                continue
            if dep not in selected_reasons:
                selected_reasons[dep] = {"DEPENDENCY_CLOSURE"}
            else:
                selected_reasons[dep].add("DEPENDENCY_CLOSURE")
            if dep not in visited:
                pending.append(dep)
        pending.sort()

    for spec in specs:
        declared = spec.get("path")
        try:
            spec_ref = _source_ref(spec)
        except ValueError:
            continue
        matches = ([observed_by_path[declared]] if declared in observed_by_path else
                   [v for k, v in sorted(observed_by_path.items())
                    if declared is None and v.get("kind") == spec.get("kind")])
        items = sorted(set(spec.get("context_items", [])))
        authoritative = spec.get("authority", "authoritative") == "authoritative"
        direct_non_authority = (not authoritative and (bool(wanted_items.intersection(items)) or
            declared in target_files or any(fact.get("path") in target_files for fact in matches)))
        relevant = spec_ref in selected_reasons or direct_non_authority
        if relevant:
            for fact in matches:
                reasons = sorted(selected_reasons.get(spec_ref) or {"EXPLICIT_DEPENDENCY_OR_TARGET"})
                sources.append({"source_ref": fact.get("path"), "kind": fact.get("kind"),
                                "authority": fact.get("authority"), "raw_sha256": fact.get("raw_sha256"),
                                "context_items": fact.get("context_items", []),
                                "reason": reasons[0], "reason_codes": reasons})
                for item in fact.get("context_items", []):
                    dependency_states.append({"context_item": item, "source_ref": fact.get("path"),
                                              "state": "CHANGED" if fact.get("path") in changed else "CURRENT"})
            if not matches:
                unresolved.append({"code": "DECLARED_SOURCE_NOT_OBSERVED", "source_ref": declared})
                if spec.get("required") is True:
                    expansion.append(_expansion(source_ref=declared,
                        reason="REQUIRED_DECLARED_SOURCE_NOT_OBSERVED",
                        requirement="REQUIRED_BEFORE_REVIEW", authority_class="authoritative"))
        else:
            omitted_sources.append({"source_ref": declared or spec.get("glob"),
                                    "reason": "PROVEN_UNRELATED_CONDITIONAL" if authoritative else
                                              "EXPLICITLY_UNRELATED_DEPENDENCY"})

    requested_findings = set(request["finding_ids"] + request["previous_finding_ids"])
    requested_evidence = set(request["evidence_ids"])
    evidence_refs, optional_candidates, omitted_evidence, found_findings = [], [], [], set()
    declared_evidence = {spec["path"]: spec for spec in config.get("sources", [])
                         if isinstance(spec, dict) and isinstance(spec.get("path"), str)
                         and spec.get("kind") == "evidence"}
    target_job_id = getattr(job, "job_id", None)
    for entry in stored_index.get("entries", []):
        findings = entry.get("findings") or []
        entry_findings = {x["finding_id"] for x in findings}
        relevance, reason_code = _classify_evidence(
            entry, capability=capability, target_job_id=target_job_id,
            requested_findings=requested_findings, requested_evidence=requested_evidence,
            declared_evidence=declared_evidence)
        ref = {key: entry.get(key) for key in (
                "evidence_id", "evidence_path", "manifest_sha256", "evidence_type", "job_id",
                "quality", "job_status", "failure_class", "actor_execution_status",
                "review_verdict", "findings", "trust", "trust_limitation", "capability",
                "review_of", "predecessor", "successor")}
        ref.update({"relevance": relevance, "relevance_reason": reason_code})
        if relevance == "REQUIRED_RELEVANT":
            evidence_refs.append(ref)
            found_findings.update(requested_findings & entry_findings)
            if entry.get("quality") in ("PARTIAL", "UNSTRUCTURED"):
                expansion.append(_expansion(evidence_ref=entry.get("evidence_id"),
                    reason="REQUIRED_RELEVANT_EVIDENCE_INCOMPLETE",
                    requirement="REQUIRED_BEFORE_REVIEW", quality=entry.get("quality"),
                    suggested_scope=[entry["evidence_path"]] if entry.get("evidence_path") else []))
        elif relevance == "BOUNDED_CANDIDATE":
            optional_candidates.append(ref)
            expansion.append(_expansion(evidence_ref=entry.get("evidence_id"),
                reason="UNKNOWN_RELEVANCE_NO_REQUIRED_STRUCTURAL_EDGE",
                requirement="OPTIONAL_BOUNDED", quality=entry.get("quality"),
                suggested_scope=[entry["evidence_path"]] if entry.get("evidence_path") else []))
        else:
            omitted_evidence.append({"evidence_id": entry.get("evidence_id"),
                                     "evidence_path": entry.get("evidence_path"),
                                     "reason": reason_code})
    for finding in sorted(requested_findings - found_findings):
        unresolved.append({"code": "REQUESTED_FINDING_NOT_STRUCTURALLY_INDEXED", "finding_id": finding})
        expansion.append(_expansion(reason=f"REQUESTED_FINDING_MISSING:{finding}",
            requirement="REQUIRED_BEFORE_REVIEW"))

    delta_status = stored_delta.get("delta_status")
    if delta_status == "POTENTIAL_AUTHORITY_CHANGE":
        reconciliation.append({"code": "CHANGED_AUTHORITY_REQUIRES_DECISION",
                               "changed_sources": sorted(changed),
                               "decision_authority": "HUMAN_IF_CRITICAL_AMBIGUOUS_OR_AUTHORITY_CHANGING"})
    elif delta_status == "UNVERIFIABLE":
        unresolved.append({"code": "DELTA_UNVERIFIABLE"})
    approved = stored_manifest.get("approved_semantics", {})
    approved_refs = []
    if approved:
        # Only explicitly provenanced values can leave this boundary.
        for key, value in sorted(approved.items()):
            if isinstance(value, dict) and value.get("approved_by") in ("ChatGPT", "Human") and value.get("source_ref"):
                approved_refs.append({"key": key, "value": value.get("value"),
                                      "source_ref": safe_relative(value["source_ref"]),
                                      "approved_by": value["approved_by"]})
            else:
                reconciliation.append({"code": "APPROVED_SEMANTIC_LACKS_PROVENANCE", "key": key})
    if delta_status == "POTENTIAL_AUTHORITY_CHANGE":
        approved_refs = []

    status = ("UNVERIFIABLE" if unresolved and any(x["code"] in
              ("INPUT_UNVERIFIABLE", "DELTA_UNVERIFIABLE", "INVALID_DECLARATION_REFERENCE") for x in unresolved)
              else "NEEDS_RECONCILIATION" if reconciliation or unresolved or any(
                  x["requirement"] == "REQUIRED_BEFORE_REVIEW" for x in expansion)
              else "READY_BOUNDED")
    selection_reasons = [
        _reason("authoritative_sources", "INCLUDED", "BASE_AUTHORITY", sum(
            "BASE_AUTHORITY" in x.get("reason_codes", []) for x in sources)),
        _reason("authoritative_sources", "INCLUDED", "TARGET_MATCH", sum(
            "TARGET_MATCH" in x.get("reason_codes", []) for x in sources)),
        _reason("authoritative_sources", "INCLUDED", "CONTEXT_ITEM_MATCH", sum(
            "CONTEXT_ITEM_MATCH" in x.get("reason_codes", []) for x in sources)),
        _reason("dependency_sources", "INCLUDED", "DEPENDENCY_CLOSURE", sum(
            "DEPENDENCY_CLOSURE" in x.get("reason_codes", []) for x in sources)),
        _reason("authoritative_sources", "INCLUDED", "CHANGED_AUTHORITY", sum(
            "CHANGED_AUTHORITY" in x.get("reason_codes", []) for x in sources)),
        _reason("conditional_authority", "EXPANSION", "MISSING_STRUCTURED_REQUEST", sum(
            "MISSING_STRUCTURED_REQUEST" in x.get("reason_codes", []) for x in sources)),
        _reason("conditional_authority", "EXPANSION", "AMBIGUOUS_SELECTOR_MAPPING", sum(
            "AMBIGUOUS_SELECTOR_MAPPING" in x.get("reason_codes", []) for x in sources)),
        _reason("unrelated_sources", "OMITTED", "PROVEN_UNRELATED_CONDITIONAL", len(omitted_sources)),
        _reason("evidence", "INCLUDED", "EXPLICIT_MATCH_OR_BOUNDED_CANONICAL_SET", len(evidence_refs)),
        _reason("approved_semantics", "INCLUDED", "EXPLICIT_APPROVAL_AND_PROVENANCE_ONLY", len(approved_refs)),
        _reason("prose", "OMITTED", "NEVER_PARSED_FOR_RELATIONSHIPS_OR_FINDINGS", 0),
    ]
    body = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "builder_version": BUILDER_VERSION,
        "mode": MODE, "workspace": workspace, "capability": capability, "phase": request["phase"],
        "selector_contract": selector_contract,
        "source_context_manifest_sha256": stored_manifest.get("lifecycle", {}).get("manifest_sha256"),
        "source_context_file_sha256": manifest_file_sha,
        "delta_report_sha256": delta_sha, "delta_status": delta_status,
        "evidence_index_sha256": index_sha,
        "job": {"job_id": getattr(job, "job_id", None), "actor": getattr(job, "actor", None),
                "mode": getattr(job, "mode", None),
                "instruction_sha256": getattr(job, "prompt_sha256", None)},
        "selection_status": status, "authoritative_source_refs": sources,
        "approved_semantics": approved_refs,
        "dependency_states": sorted(dependency_states, key=lambda x: (x["context_item"], x["source_ref"])),
        "evidence_refs": sorted(evidence_refs, key=lambda x: (x["evidence_id"], x["evidence_path"])),
        "optional_evidence_candidates": sorted(optional_candidates,
            key=lambda x: (x["evidence_id"], x["evidence_path"])),
        "omitted_irrelevant_evidence": sorted(omitted_evidence,
            key=lambda x: (x["evidence_id"], x["evidence_path"])),
        "requested_finding_ids": request["finding_ids"],
        "previous_finding_ids": request["previous_finding_ids"],
        "target_files": request["target_files"],
        "changed_sources": [changed[k] for k in sorted(changed)],
        "boundaries": {"protected_paths": _strings(config.get("protected_paths"), "protected_paths"),
                       "forbidden_paths": _strings(config.get("forbidden_paths"), "forbidden_paths")},
        "unresolved_items": sorted(unresolved, key=lambda x: canonical(x)),
        "reconciliation_requirements": sorted(reconciliation, key=lambda x: canonical(x)),
        "expansion_requirements": sorted(expansion, key=lambda x: canonical(x)),
        "included_hashes": {
            "sources": sorted(({"path": x["source_ref"], "sha256": x["raw_sha256"]} for x in sources), key=lambda x: x["path"]),
            "evidence_files": sorted((f for e in stored_index.get("entries", []) for f in e.get("source_files", [])
                                      if any(r["evidence_id"] == e["evidence_id"] for r in evidence_refs)),
                                     key=lambda x: (x["path"], x["sha256"])),
            "excerpts": [],
        },
        "omitted_sources": omitted_sources, "selection_reasons": selection_reasons,
        "authority_decision": {"llm_role": "MAY_PROPOSE_RECONCILIATION",
                               "normal_acceptance_authority": "ChatGPT",
                               "escalation_authority": "Human",
                               "phase_2b_mutates_approved_semantics": False},
    }
    payload_bytes = sum(x.get("size", 0) for x in observed_by_path.values()
                        if any(s["source_ref"] == x.get("path") for s in sources))
    budget = request.get("max_text_bytes")
    if budget is not None and (not isinstance(budget, int) or isinstance(budget, bool) or budget < 0):
        body["unresolved_items"].append({"code": "INVALID_SIZE_BUDGET"})
        body["selection_status"] = "UNVERIFIABLE"
    elif budget is not None and payload_bytes > budget:
        body["expansion_requirements"].append(_expansion(
            reason="SIZE_BUDGET_CANNOT_TRUNCATE_REQUIRED_AUTHORITY",
            requirement="REQUIRED_BEFORE_REVIEW", authority_class="authoritative"))
        if body["selection_status"] == "READY_BOUNDED":
            body["selection_status"] = "NEEDS_RECONCILIATION"
    body["size"] = {"source_ref_count": len(sources), "evidence_ref_count": len(evidence_refs),
                    "file_hash_count": len(body["included_hashes"]["sources"]) + len(body["included_hashes"]["evidence_files"]),
                    "estimated_textual_payload_bytes": payload_bytes,
                     "configured_max_text_bytes": budget}
    required_expansions = [x for x in body["expansion_requirements"]
                           if x["requirement"] == "REQUIRED_BEFORE_REVIEW"]
    optional_expansions = [x for x in body["expansion_requirements"]
                           if x["requirement"] == "OPTIONAL_BOUNDED"]
    reason_counts = {}
    for item in body["expansion_requirements"]:
        reason_counts[item["reason_code"]] = reason_counts.get(item["reason_code"], 0) + 1
    body["diagnostics"] = {"required_expansion_count": len(required_expansions),
        "optional_candidate_count": len(optional_candidates),
        "omitted_irrelevant_count": len(omitted_evidence),
        "expansion_reason_code_counts": dict(sorted(reason_counts.items()))}
    package_hash = sha256(canonical(body))
    result = dict(body)
    result["package_sha256"] = package_hash
    result["manifest_sha256"] = package_hash
    report = {"mode": MODE, "status": result["selection_status"],
              "schema_version": SCHEMA_VERSION, "sha256": sha256(canonical(result)),
              "package_sha256": package_hash,
              "source_context_sha256": result["source_context_manifest_sha256"],
              "evidence_index_sha256": result["evidence_index_sha256"],
              "delta_status": delta_status, "authority_ref_count": len(sources),
              "evidence_ref_count": len(evidence_refs),
              "expansion_required_count": len(required_expansions),
              "required_expansion_count": len(required_expansions),
              "optional_candidate_count": len(optional_candidates),
              "omitted_irrelevant_count": len(omitted_evidence),
              "reason_code_counts": result["diagnostics"]["expansion_reason_code_counts"],
              "bytes": len(canonical(result))}
    return result, report


def generate(root: str | Path, **kwargs) -> dict:
    root = Path(root).resolve()
    declaration = kwargs["declaration"]
    capability = kwargs["capability"]
    generated = safe_relative(declaration.get("generated_root", "validation/context"))
    destination = root / generated / capability
    destination.mkdir(parents=True, exist_ok=True)
    context, report = build(root, **kwargs)
    context_path = destination / "job-context.json"
    report_path = destination / "job-context-report.json"
    context_path.write_bytes(canonical(context))
    report["report_path"] = report_path.relative_to(root).as_posix()
    report["job_context_path"] = context_path.relative_to(root).as_posix()
    report_path.write_bytes(canonical(report))
    return report
