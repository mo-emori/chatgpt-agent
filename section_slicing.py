"""Deterministic exact-byte Markdown authority section slicing v1."""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or ":" in value:
        raise ValueError(f"unsafe workspace-relative path: {value!r}")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe workspace-relative path: {value!r}")
    return path.as_posix()


COMPLETE = "COMPLETE_MAPPED"
WHOLE = "WHOLE_FILE"
_HEADING = re.compile(br"^(#{1,6})[ \t]+([^\r\n]*?)[ \t]*(?:\r?\n|\r|$)", re.MULTILINE)


class SectionError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Heading:
    start: int
    level: int
    text: str


def validate_contract(spec: dict, ref: str) -> None:
    sections = spec.get("sections")
    coverage = spec.get("section_coverage")
    if sections is None and coverage is None:
        return
    if sections is None:
        raise ValueError(f"section_coverage requires sections: {ref}")
    if not isinstance(sections, list) or not sections:
        raise ValueError(f"sections must be a non-empty list: {ref}")
    if coverage is not None and coverage != COMPLETE:
        raise ValueError(f"unknown section_coverage: {ref}")
    if "glob" in spec:
        raise ValueError(f"section slicing requires a stable path source: {ref}")
    seen = set()
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError(f"section must be an object: {ref}")
        section_id = section.get("section_id")
        if not isinstance(section_id, str) or not section_id or section_id in seen:
            raise ValueError(f"section_id must be a unique non-empty string: {ref}")
        seen.add(section_id)
        boundary = section.get("boundary")
        if not isinstance(boundary, dict) or boundary.get("kind") not in ("heading", "preamble"):
            raise ValueError(f"invalid section boundary: {ref}#{section_id}")
        if boundary["kind"] == "preamble":
            if set(boundary) != {"kind"}:
                raise ValueError(f"preamble boundary accepts only kind: {ref}#{section_id}")
        else:
            if not isinstance(boundary.get("heading"), str) or not boundary["heading"]:
                raise ValueError(f"heading boundary requires heading: {ref}#{section_id}")
            if not isinstance(boundary.get("level"), int) or isinstance(boundary["level"], bool) or not 1 <= boundary["level"] <= 6:
                raise ValueError(f"heading boundary level must be 1..6: {ref}#{section_id}")
            if "occurrence" in boundary and (not isinstance(boundary["occurrence"], int)
                    or isinstance(boundary["occurrence"], bool) or boundary["occurrence"] <= 0):
                raise ValueError(f"heading occurrence must be positive: {ref}#{section_id}")
            if set(boundary) - {"kind", "heading", "level", "occurrence"}:
                raise ValueError(f"unknown heading boundary field: {ref}#{section_id}")
        if "always_required" in section and not isinstance(section["always_required"], bool):
            raise ValueError(f"section always_required must be boolean: {ref}#{section_id}")
        if "context_items" not in section:
            raise ValueError(f"section context_items must be explicit: {ref}#{section_id}")
        for field in ("context_items", "target_files", "depends_on"):
            values = section.get(field, [])
            if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values):
                raise ValueError(f"section {field} must be non-empty strings: {ref}#{section_id}")
        for target in section.get("target_files", []):
            safe_relative(target)
    for section in sections:
        unknown = set(section.get("depends_on", [])) - seen
        if unknown:
            raise ValueError(f"unknown section dependency: {ref}#{section['section_id']}->{sorted(unknown)[0]}")


def _headings(raw: bytes) -> list[Heading]:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SectionError("SECTION_SLICING_UNSUPPORTED_SOURCE", str(exc)) from exc
    result = []
    for match in _HEADING.finditer(raw):
        try:
            text = match.group(2).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SectionError("SECTION_SLICING_UNSUPPORTED_SOURCE", str(exc)) from exc
        result.append(Heading(match.start(), len(match.group(1)), text))
    return result


def resolve(raw: bytes, sections: list[dict]) -> list[dict]:
    headings = _headings(raw)
    resolved = []
    for order, section in enumerate(sections):
        boundary = section["boundary"]
        if boundary["kind"] == "preamble":
            start, end = 0, headings[0].start if headings else len(raw)
        else:
            matches = [h for h in headings if h.level == boundary["level"] and h.text == boundary["heading"]]
            occurrence = boundary.get("occurrence")
            if occurrence is None and len(matches) > 1:
                raise SectionError("SECTION_BOUNDARY_AMBIGUOUS", section["section_id"])
            if not matches or (occurrence is not None and occurrence > len(matches)):
                raise SectionError("SECTION_BOUNDARY_MISSING", section["section_id"])
            heading = matches[0 if occurrence is None else occurrence - 1]
            start = heading.start
            end = next((h.start for h in headings if h.start > start and h.level <= heading.level), len(raw))
        payload = raw[start:end]
        resolved.append({"section_id": section["section_id"], "boundary": boundary,
            "start_byte": start, "end_byte": end, "slice_sha256": sha256(payload),
            "slice_payload": base64.b64encode(payload).decode("ascii"),
            "context_items": sorted(set(section.get("context_items", []))),
            "target_files": sorted(set(section.get("target_files", []))),
            "depends_on": sorted(set(section.get("depends_on", []))),
            "always_required": section.get("always_required", False),
            "declaration_order": order})
    # Equal/nested ranges are valid and are merged after selection. Crossing ranges
    # cannot arise from the structural rule, but reject them defensively.
    ordered = sorted(resolved, key=lambda x: (x["start_byte"], x["end_byte"]))
    for left, right in zip(ordered, ordered[1:]):
        if left["start_byte"] < right["start_byte"] < left["end_byte"] < right["end_byte"]:
            raise SectionError("SECTION_OVERLAP_INVALID")
    return resolved


def select(spec: dict, raw: bytes, *, context_items: list[str], target_files: list[str]) -> dict:
    whole = {"coverage_status": WHOLE, "sections": [], "diagnostics": []}
    if not spec.get("sections"):
        return whole
    if spec.get("section_coverage") != COMPLETE:
        return {**whole, "coverage_status": "SECTION_COVERAGE_INCOMPLETE",
                "diagnostics": [{"code": "SECTION_COVERAGE_INCOMPLETE"}]}
    kind = spec.get("kind", "").lower()
    path = spec.get("path", "").lower()
    if kind not in ("markdown", "design", "contract", "adr", "documentation", "doc") and not path.endswith((".md", ".markdown")):
        return {**whole, "coverage_status": "SECTION_SLICING_UNSUPPORTED_SOURCE",
                "diagnostics": [{"code": "SECTION_SLICING_UNSUPPORTED_SOURCE"}]}
    requested_items, requested_targets = set(context_items), set(target_files)
    mapped_items = {x for s in spec["sections"] for x in s.get("context_items", [])}
    mapped_targets = {x for s in spec["sections"] for x in s.get("target_files", [])}
    if requested_items - mapped_items or requested_targets - mapped_targets:
        return {**whole, "coverage_status": "SECTION_COVERAGE_INCOMPLETE",
                "diagnostics": [{"code": "SECTION_COVERAGE_INCOMPLETE",
                    "unmapped_context_items": sorted(requested_items - mapped_items),
                    "unmapped_target_files": sorted(requested_targets - mapped_targets)}]}
    try:
        resolved = resolve(raw, spec["sections"])
    except SectionError as exc:
        return {**whole, "coverage_status": exc.code,
                "diagnostics": [{"code": exc.code, "detail": exc.detail}]}
    by_id = {x["section_id"]: x for x in resolved}
    selected, reasons = set(), {}
    for section in resolved:
        sid = section["section_id"]
        if section["always_required"]:
            selected.add(sid); reasons.setdefault(sid, set()).add("ALWAYS_REQUIRED")
        if requested_items.intersection(section["context_items"]):
            selected.add(sid); reasons.setdefault(sid, set()).add("CONTEXT_ITEM_MATCH")
        if requested_targets.intersection(section["target_files"]):
            selected.add(sid); reasons.setdefault(sid, set()).add("TARGET_FILE_MATCH")
    if not (requested_items or requested_targets) and not selected:
        return {**whole, "coverage_status": "SECTION_COVERAGE_INCOMPLETE",
                "diagnostics": [{"code": "SECTION_COVERAGE_INCOMPLETE", "detail": "empty request has no safe base section"}]}
    pending = sorted(selected)
    while pending:
        sid = pending.pop(0)
        for dep in by_id[sid]["depends_on"]:
            if dep not in selected:
                selected.add(dep); pending.append(dep)
            reasons.setdefault(dep, set()).add("DEPENDENCY_CLOSURE")
        pending.sort()
    chosen = []
    for section in resolved:
        if section["section_id"] in selected:
            item = dict(section)
            item["selection_reasons"] = sorted(reasons[section["section_id"]])
            chosen.append(item)
    return {"coverage_status": COMPLETE, "sections": chosen, "diagnostics": []}


def merge_selected(sections: list[dict], raw: bytes) -> list[dict]:
    """Merge overlap/adjacency without duplicated payload, retaining attribution."""
    merged = []
    for section in sorted(sections, key=lambda x: (x["start_byte"], x["end_byte"], x["declaration_order"])):
        if merged and section["start_byte"] < merged[-1]["end_byte"]:
            item = merged[-1]
            item["end_byte"] = max(item["end_byte"], section["end_byte"])
            item["section_ids"].append(section["section_id"])
            item["boundaries"].append(section["boundary"])
            item["context_items"] = sorted(set(item["context_items"] + section["context_items"]))
            item["selection_reasons"] = sorted(set(item["selection_reasons"] + section["selection_reasons"]))
        else:
            merged.append({"start_byte": section["start_byte"], "end_byte": section["end_byte"],
                "section_ids": [section["section_id"]], "boundaries": [section["boundary"]],
                "context_items": list(section["context_items"]),
                "selection_reasons": list(section["selection_reasons"])})
    for item in merged:
        payload = raw[item["start_byte"]:item["end_byte"]]
        item["slice_sha256"] = sha256(payload)
        item["slice_payload"] = base64.b64encode(payload).decode("ascii")
    return merged
