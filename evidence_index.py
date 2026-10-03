"""Deterministic Phase-2A index of canonical, normalized evidence packages.

Evidence is data.  This module never executes it and never extracts semantics
from prose.  The canonical index deliberately excludes timestamps and cache
statistics so identical source bytes produce identical index bytes and hash.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath


SCHEMA = "context-harness-evidence-index"
SCHEMA_VERSION = 1
BUILDER_VERSION = "context-harness-phase2a-1"
MANIFEST_NAMES = ("review-manifest.json", "job-evidence-manifest.json")
QUALITY = ("STRUCTURED", "PARTIAL", "UNSTRUCTURED")


def _canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("path must be a non-empty string")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe workspace-relative path: {value!r}")
    if ":" in value:
        raise ValueError(f"unsafe workspace-relative path: {value!r}")
    return path.as_posix()


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0)
                                     & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _safe_file(root: Path, rel: str) -> Path:
    rel = _safe_relative(rel)
    target = root / Path(rel)
    current = root
    for part in Path(rel).parts:
        current = current / part
        if current.exists() and _is_link(current):
            raise ValueError(f"symlink/reparse source rejected: {rel}")
    if not target.is_file():
        raise ValueError(f"required canonical evidence source missing: {rel}")
    return target


def _load_json(data: bytes, rel: str) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed canonical JSON: {rel}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"canonical JSON must be an object: {rel}")
    return value


def discover_sources(root: str | Path, source_roots: list[dict],
                     declared_sources: list[dict] | None = None) -> list[dict]:
    """Discover only explicitly configured canonical package/result sources."""
    root = Path(root).resolve()
    found = {}
    for spec in source_roots:
        rel_root = _safe_relative(spec["path"])
        expected = spec.get("manifest_name")
        if expected not in MANIFEST_NAMES:
            raise ValueError(f"unsupported canonical manifest name: {expected!r}")
        directory = root / Path(rel_root)
        if directory.exists() and _is_link(directory):
            raise ValueError(f"symlink/reparse evidence root rejected: {rel_root}")
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise ValueError(f"canonical evidence root is not a directory: {rel_root}")
        for child in sorted(directory.iterdir(), key=lambda p: p.name):
            if _is_link(child):
                raise ValueError(f"symlink/reparse package rejected: {child.relative_to(root).as_posix()}")
            if not child.is_dir():
                continue
            manifest = child / expected
            if not manifest.exists():
                if not child.name.startswith("."):
                    raise ValueError(
                        f"canonical evidence package lacks {expected}: "
                        f"{child.relative_to(root).as_posix()}")
                continue
            rel = manifest.relative_to(root).as_posix()
            found[rel] = {"path": rel, "kind": spec["kind"], "required": True}
    for spec in declared_sources or []:
        if spec.get("canonical_evidence") is not True:
            continue
        rel = _safe_relative(spec["path"])
        found[rel] = {"path": rel, "kind": spec.get("evidence_type", "job_result"),
                      "required": bool(spec.get("required", True))}
    return [found[key] for key in sorted(found)]


def _nullable(value, key):
    return value.get(key) if isinstance(value, dict) else None


def _structured_findings(value: dict):
    findings = value.get("findings")
    if not isinstance(findings, list):
        return None
    result = []
    for finding in findings:
        if not isinstance(finding, dict) or not isinstance(finding.get("finding_id"), str):
            raise ValueError("structured findings require finding_id")
        result.append({"finding_id": finding["finding_id"], "status": finding.get("status")})
    return sorted(result, key=lambda item: (item["finding_id"], str(item["status"])))


def _base_entry(source_rel: str, source_sha: str, kind: str) -> dict:
    return {
        "evidence_id": f"{kind}:{source_sha}", "evidence_type": kind,
        "job_id": None, "actor": None, "mode": None, "workspace": None,
        "capability": None, "job_status": None, "failure_class": None,
        "artifact_status": None, "evidence_path": source_rel,
        "manifest_sha256": source_sha, "baseline_head": None, "head": None,
        "canonical_head": None, "instruction_sha256": None,
        "prompt_provenance": None, "review_boundary": None,
        "actor_execution_status": None, "review_verdict": None,
        "findings": None, "predecessor": None, "successor": None,
        "review_of": None, "trust": [], "trust_limitation": None,
        "quality": "PARTIAL", "source_files": [], "provenance": None,
        "noncanonical_references": [],
    }


def _verified_files(root: Path, manifest_rel: str, manifest: dict,
                    source_sha: str, references: list[dict]) -> tuple[list[dict], dict[str, bytes]]:
    package = PurePosixPath(manifest_rel).parent
    facts = [{"path": manifest_rel, "sha256": source_sha}]
    content = {}
    for ref in references:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            raise ValueError(f"invalid referenced file in {manifest_rel}")
        rel = (package / _safe_relative(ref["path"])).as_posix()
        data = _safe_file(root, rel).read_bytes()
        actual = _sha(data)
        if ref.get("sha256") != actual:
            raise ValueError(f"referenced evidence hash mismatch: {rel}")
        facts.append({"path": rel, "sha256": actual})
        content[ref["path"]] = data
    return sorted(facts, key=lambda item: item["path"]), content


def _index_review(root: Path, rel: str, raw: bytes, manifest: dict) -> dict:
    if manifest.get("schema") != "worker-review-evidence" or manifest.get("version") != 1:
        raise ValueError(f"unsupported review evidence schema: {rel}")
    entry = _base_entry(rel, _sha(raw), "review")
    files, content = _verified_files(root, rel, manifest, entry["manifest_sha256"],
                                     manifest.get("normalized_files", []))
    execution = _load_json(content["review-execution.json"], "review-execution.json")
    findings = _structured_findings(execution)
    actor_status = manifest.get("actor_status")
    failed = actor_status != "DONE" or execution.get("exit_code") not in (0, None)
    verdict = execution.get("review_verdict") if not failed else None
    if verdict is not None and not isinstance(verdict, str):
        raise ValueError("review_verdict must be a string")
    entry.update({
        "job_id": manifest.get("job_id"), "actor": manifest.get("actor"),
        "mode": manifest.get("mode"), "canonical_head": manifest.get("canonical_head"),
        "head": _nullable(manifest.get("review"), "head_after"),
        "baseline_head": _nullable(manifest.get("review"), "head_before"),
        "review_boundary": manifest.get("review_boundary"),
        "actor_execution_status": actor_status, "review_verdict": verdict,
        "findings": findings, "trust": ["WORKER_OBSERVED", "ACTOR_REPORTED"],
        "source_files": files,
        "provenance": {"source": manifest.get("source_provenance"),
                       "adoption": manifest.get("adoption")},
        "noncanonical_references": manifest.get("raw_local_only", []),
    })
    # A final prose result is not parsed into findings or a verdict.
    if findings is not None and verdict is not None:
        entry["quality"] = "STRUCTURED"
    elif execution.get("final_result_text") and findings is None:
        entry["quality"] = "UNSTRUCTURED"
    else:
        entry["quality"] = "PARTIAL"
    return entry


def _index_historical(root: Path, rel: str, raw: bytes, manifest: dict) -> dict:
    if manifest.get("schema") != "worker-job-evidence" or manifest.get("version") != 1:
        raise ValueError(f"unsupported historical evidence schema: {rel}")
    entry = _base_entry(rel, _sha(raw), "historical_job")
    refs = manifest.get("adopted_files", [])
    files, content = _verified_files(root, rel, manifest, entry["manifest_sha256"], refs)
    worker_ref = manifest.get("worker_observed", {})
    if worker_ref.get("sha256") != next((x.get("sha256") for x in refs
                                         if x.get("path") == worker_ref.get("file")), None):
        raise ValueError(f"worker_observed hash mismatch: {rel}")
    normalized = _load_json(content[worker_ref["file"]], worker_ref["file"])
    observed = normalized.get("worker_observed")
    if not isinstance(observed, dict):
        raise ValueError(f"missing worker_observed structure: {rel}")
    git = observed.get("git") if isinstance(observed.get("git"), dict) else {}
    entry.update({
        "job_id": manifest.get("job_id"), "actor": manifest.get("actor"),
        "mode": manifest.get("mode"), "workspace": manifest.get("workspace"),
        "job_status": observed.get("status"), "failure_class": observed.get("failure_class"),
        "artifact_status": observed.get("artifact_status"),
        "baseline_head": git.get("baseline_commit"), "head": git.get("head_after"),
        "instruction_sha256": manifest.get("instruction_sha256"),
        "prompt_provenance": manifest.get("instruction_ref"),
        "trust": ["WORKER_OBSERVED", "ACTOR_REPORTED", "CORROBORATIVE_ONLY",
                  "HISTORICAL_MANUAL"],
        "trust_limitation": manifest.get("trust_limitation"), "source_files": files,
        "provenance": {"source": manifest.get("source_provenance"),
                       "adoption": manifest.get("adoption"),
                       "corroboration": manifest.get("corroboration")},
        "noncanonical_references": manifest.get("raw_local_only", []),
        "quality": "PARTIAL",
    })
    return entry


def _index_result(rel: str, raw: bytes, value: dict, kind: str) -> dict:
    entry = _base_entry(rel, _sha(raw), kind)
    git = value.get("git") if isinstance(value.get("git"), dict) else {}
    boundary = value.get("review_boundary")
    execution = value.get("review_execution")
    actor_status = _nullable(execution, "actor_status")
    failed = value.get("status") != "DONE" or actor_status not in (None, "DONE")
    findings = _structured_findings(value)
    verdict = value.get("review_verdict") if not failed else None
    entry.update({
        "job_id": value.get("job_id"), "actor": value.get("actor"),
        "mode": value.get("mode"), "workspace": value.get("workspace"),
        "capability": value.get("capability"), "job_status": value.get("status"),
        "failure_class": value.get("failure_class"),
        "artifact_status": value.get("artifact_status"),
        "baseline_head": git.get("baseline_commit"), "head": git.get("head_after"),
        "canonical_head": value.get("canonical_head"),
        "instruction_sha256": value.get("instruction_sha256", value.get("prompt_sha256")),
        "prompt_provenance": value.get("instruction_ref"),
        "review_boundary": _nullable(boundary, "status"),
        "actor_execution_status": actor_status, "review_verdict": verdict,
        "findings": findings, "predecessor": value.get("predecessor"),
        "successor": value.get("successor"), "review_of": value.get("review_of"),
        "trust": ["WORKER_OBSERVED"],
        "source_files": [{"path": rel, "sha256": _sha(raw)}],
    })
    semantic_prose = bool(value.get("summary") or _nullable(execution, "final_result_text"))
    required = all(value.get(key) is not None for key in ("job_id", "actor", "mode", "status"))
    entry["quality"] = ("STRUCTURED" if (findings is not None or (kind != "review" and required))
                        else "UNSTRUCTURED" if semantic_prose else "PARTIAL")
    return entry


def _parse_source(root: Path, spec: dict, raw: bytes) -> dict:
    rel, kind = spec["path"], spec["kind"]
    value = _load_json(raw, rel)
    if value.get("schema") == "worker-review-evidence":
        return _index_review(root, rel, raw, value)
    if value.get("schema") == "worker-job-evidence":
        return _index_historical(root, rel, raw, value)
    return _index_result(rel, raw, value, kind)


def build_index(root: str | Path, *, workspace: str, capability: str,
                sources: list[dict], cache_path: str | Path | None = None) -> tuple[dict, dict]:
    root = Path(root).resolve()
    prior = {}
    if cache_path and Path(cache_path).is_file():
        try:
            cached = json.loads(Path(cache_path).read_text("utf-8"))
            if cached.get("schema_version") == SCHEMA_VERSION:
                prior = cached.get("sources", {})
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            prior = {}
    current_cache, entries, errors = {}, [], []
    reused = changed = 0
    for spec in sorted(sources, key=lambda item: item["path"]):
        rel = spec["path"]
        try:
            raw = _safe_file(root, rel).read_bytes()
            source_sha = _sha(raw)
            old = prior.get(rel)
            if old and old.get("source_sha256") == source_sha:
                entry = old["entry"]
                # Reverify every referenced byte before reuse; parsing is skipped.
                for fact in entry.get("source_files", []):
                    if _sha(_safe_file(root, fact["path"]).read_bytes()) != fact["sha256"]:
                        raise ValueError(f"stale cached dependency: {fact['path']}")
                reused += 1
            else:
                entry = _parse_source(root, spec, raw)
                changed += 1
            current_cache[rel] = {"source_sha256": source_sha, "entry": entry}
            entries.append(entry)
        except Exception as exc:
            errors.append({"path": rel, "code": "SOURCE_INVALID", "detail": str(exc)[:1000]})
    removed = sorted(set(prior) - {item["path"] for item in sources})
    index = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION, "workspace": workspace,
        "capability": capability, "status": "FAILED" if errors else "READY",
        "entries": sorted(entries, key=lambda item: (item["evidence_id"], item["evidence_path"])),
        "source_manifest": sorted(
            ({"path": rel, "sha256": data["source_sha256"]}
             for rel, data in current_cache.items()), key=lambda item: item["path"]),
        "diagnostics": errors,
    }
    index_bytes = _canonical(index)
    report = {
        "schema_version": SCHEMA_VERSION, "status": index["status"],
        "quality_counts": {quality: sum(e["quality"] == quality for e in entries)
                           for quality in QUALITY},
        "entry_count": len(entries), "index_sha256": _sha(index_bytes),
        "source_count": len(sources), "reused_source_count": reused,
        "changed_source_count": changed, "removed_sources": removed,
        "diagnostics": errors,
    }
    if cache_path:
        cache = Path(cache_path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        temp = cache.with_suffix(cache.suffix + ".tmp")
        temp.write_bytes(_canonical({"schema_version": SCHEMA_VERSION,
                                     "sources": current_cache}))
        os.replace(temp, cache)
    return index, report


def generate(root: str | Path, *, workspace: str, capability: str,
             source_roots: list[dict], declared_sources: list[dict] | None,
             generated_root: str, cache_path: str | Path) -> dict:
    root = Path(root).resolve()
    destination_rel = _safe_relative(f"{generated_root}/{capability}")
    destination = root / Path(destination_rel)
    try:
        sources = discover_sources(root, source_roots, declared_sources)
        index, report = build_index(root, workspace=workspace, capability=capability,
                                    sources=sources, cache_path=cache_path)
        destination.mkdir(parents=True, exist_ok=True)
        index_path = destination / "evidence-index.json"
        report_path = destination / "evidence-index-report.json"
        index_path.write_bytes(_canonical(index))
        report["report_path"] = report_path.relative_to(root).as_posix()
        report_path.write_bytes(_canonical(report))
        return report
    except Exception as exc:
        report = {"schema_version": SCHEMA_VERSION, "status": "FAILED",
                "quality_counts": {quality: 0 for quality in QUALITY},
                "entry_count": 0, "index_sha256": None, "source_count": 0,
                "reused_source_count": 0, "changed_source_count": 0,
                "report_path": None,
                "diagnostics": [{"code": "INDEX_BUILD_FAILED", "detail": str(exc)[:1000]}]}
        try:
            destination.mkdir(parents=True, exist_ok=True)
            report_path = destination / "evidence-index-report.json"
            report["report_path"] = report_path.relative_to(root).as_posix()
            report_path.write_bytes(_canonical(report))
        except OSError:
            pass
        return report
