"""Strict normalized ACTOR_REPORTED review-decision evidence."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath

SCHEMA = "normalized-review-decision"
VERSION = 1
BEGIN = "<REVIEW_DECISION>"
END = "</REVIEW_DECISION>"
VERDICTS = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "INCONCLUSIVE"}
FINDING_STATUSES = {"OPEN", "CLOSED", "RESOLVED", "ACCEPTED", "REJECTED", "DEFERRED", "SUPERSEDED"}
SEVERITIES = {"CRITICAL", "HIGH", "MODERATE", "MEDIUM", "MINOR", "LOW", "INFO"}
OUTCOMES = {"COMPLETE", "PACKAGE_INSUFFICIENT", "NEEDS_FULL_REVIEW"}
MAX_BYTES = 256_000
MAX_FINDINGS = 200
MAX_REFS = 100

class ReviewDecisionError(ValueError): pass

def sha256(data: bytes) -> str: return hashlib.sha256(data).hexdigest()

def _text(value, label, maximum=4000, required=False):
    if value is None and not required: return None
    if not isinstance(value, str) or (required and not value) or len(value) > maximum or "\x00" in value:
        raise ReviewDecisionError(f"invalid {label}")
    return value

def _ref(value, label):
    value = _text(value, label, 500, True)
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ":" in value or any(p in ("", ".", "..") for p in path.parts):
        raise ReviewDecisionError(f"unsafe {label}")
    return path.as_posix()

def _refs(value, label):
    if value is None: return []
    if not isinstance(value, list) or len(value) > MAX_REFS: raise ReviewDecisionError(f"invalid {label}")
    return [_ref(x, label) for x in value]

def validate(value: dict, *, review_job_id=None, workspace=None, capability=None) -> dict:
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("schema_version") != VERSION:
        raise ReviewDecisionError("unsupported review decision schema")
    allowed = {"schema", "schema_version", "review_job_id", "actor", "workspace", "capability",
        "review_mode", "review_of", "target_job_id", "target_baseline", "target_head",
        "target_instruction_sha256", "package_hash", "verdict", "package_sufficiency",
        "findings", "expansion_result", "actor_usage", "provenance", "trust_class"}
    if set(value) - allowed: raise ReviewDecisionError("unknown review decision fields")
    out = dict(value)
    for key in ("review_job_id", "actor", "workspace", "review_mode"):
        out[key] = _text(value.get(key), key, 300, True)
    if review_job_id and out["review_job_id"] != review_job_id: raise ReviewDecisionError("wrong review job identity")
    if workspace and out["workspace"] != workspace: raise ReviewDecisionError("wrong workspace")
    if capability is not None and value.get("capability") != capability: raise ReviewDecisionError("wrong capability")
    out["capability"] = _text(value.get("capability"), "capability", 300)
    if value.get("trust_class") != "ACTOR_REPORTED": raise ReviewDecisionError("trust_class must be ACTOR_REPORTED")
    if value.get("verdict") not in VERDICTS: raise ReviewDecisionError("invalid verdict")
    for key in ("review_of", "target_job_id", "target_baseline", "target_head", "target_instruction_sha256", "package_hash"):
        out[key] = _text(value.get(key), key, 300)
    suff = value.get("package_sufficiency")
    if suff is not None and suff not in OUTCOMES: raise ReviewDecisionError("invalid package_sufficiency")
    findings = value.get("findings")
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS: raise ReviewDecisionError("invalid findings")
    ids, normalized = set(), []
    for item in findings:
        if not isinstance(item, dict): raise ReviewDecisionError("invalid finding")
        if set(item) - {"finding_id","severity","category","status","summary","title","affected_paths","authority_refs","evidence_refs","predecessor","disposition","recommendation","trust_class"}:
            raise ReviewDecisionError("unknown finding fields")
        fid = _text(item.get("finding_id"), "finding_id", 200, True)
        if fid in ids: raise ReviewDecisionError("duplicate finding_id")
        ids.add(fid)
        if item.get("severity") is not None and item["severity"] not in SEVERITIES: raise ReviewDecisionError("invalid severity")
        if item.get("status") is not None and item["status"] not in FINDING_STATUSES: raise ReviewDecisionError("invalid finding status")
        if item.get("trust_class") != "ACTOR_REPORTED": raise ReviewDecisionError("finding trust_class must be ACTOR_REPORTED")
        row = dict(item)
        row["finding_id"] = fid
        for key in ("category","summary","title","predecessor","disposition","recommendation"):
            row[key] = _text(item.get(key), key)
        for key in ("affected_paths","authority_refs","evidence_refs"):
            row[key] = _refs(item.get(key), key)
        normalized.append(row)
    out["findings"] = normalized
    if not isinstance(value.get("provenance"), dict): raise ReviewDecisionError("provenance must be an object")
    return out

def parse_block(text: str, **identity) -> tuple[dict | None, str | None]:
    """Return (decision,error); absence is not an execution error."""
    if not isinstance(text, str): return None, None
    matches = re.findall(re.escape(BEGIN) + r"\s*(.*?)\s*" + re.escape(END), text, re.S)
    if not matches: return None, None
    if len(matches) != 1: return None, "multiple REVIEW_DECISION blocks"
    raw = matches[0].encode("utf-8")
    if len(raw) > MAX_BYTES: return None, "review decision exceeds size limit"
    try: return validate(json.loads(raw), **identity), None
    except (json.JSONDecodeError, ReviewDecisionError) as exc: return None, str(exc)
