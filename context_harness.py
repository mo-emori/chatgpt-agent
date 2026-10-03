"""Deterministic Phase-1 Capability Context observation and shadow delta scan.

This module deliberately observes bytes and configured relationships only.  It
does not interpret, approve, or inject semantics into an actor prompt.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath

from evidence_index import generate as generate_evidence_index
from job_context import generate as generate_job_context


SCHEMA_VERSION = 1
BUILDER_VERSION = "context-harness-phase1-1"
DECLARATION = Path(".agent/context.json")
STATES = ("NO_IMPACT", "CONTEXT_UPDATE", "POTENTIAL_AUTHORITY_CHANGE", "UNVERIFIABLE")


def _canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=15,
    )
    if completed.returncode:
        raise OSError(completed.stderr.strip() or "git command failed")
    return completed.stdout.rstrip("\r\n")


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(p in ("", ".", "..") for p in path.parts):
        raise ValueError(f"unsafe repo-relative path: {value!r}")
    return path.as_posix()


def load_declaration(root: str | Path) -> tuple[dict | None, str | None, list[str]]:
    root = Path(root).resolve()
    path = root / DECLARATION
    if not path.exists():
        return None, None, []
    errors = []
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
        if value.get("schema_version") != 1 or value.get("mode") != "SHADOW":
            raise ValueError("schema_version=1 and mode=SHADOW are required")
        if not isinstance(value.get("capabilities"), dict):
            raise ValueError("capabilities must be an object")
        # approved semantics cannot be declared by an actor-editable mapping.
        if "approved_semantics" in value:
            raise ValueError("approved_semantics is not allowed in a workspace declaration")
        return value, _sha(raw), []
    except Exception as exc:
        errors.append(f"DECLARATION_UNREADABLE: {exc}")
        return None, None, errors


def select_capability(declaration: dict, *, actor: str, mode: str) -> str | None:
    matches = []
    for capability, config in declaration["capabilities"].items():
        selectors = config.get("selectors", {})
        actors = selectors.get("actors", [actor])
        modes = selectors.get("modes", [mode])
        if actor in actors and mode in modes:
            matches.append(capability)
    return matches[0] if len(matches) == 1 else None


def _file_fact(root: Path, rel: str, spec: dict) -> tuple[dict, str | None]:
    target = root / Path(rel)
    fact = {
        "path": rel, "kind": spec["kind"],
        "authority": spec.get("authority", "authoritative"),
        "context_items": sorted(set(spec.get("context_items", []))),
        "required": bool(spec.get("required", True)),
    }
    try:
        if not target.exists():
            fact.update({"exists": False, "type": "missing", "raw_sha256": None})
            return fact, (f"MISSING_REQUIRED_SOURCE: {rel}" if fact["required"] else None)
        if target.is_symlink() or not target.is_file():
            fact.update({"exists": True, "type": "unsupported", "raw_sha256": None})
            return fact, f"UNSAFE_SOURCE_TYPE: {rel}"
        raw = target.read_bytes()
        fact.update({"exists": True, "type": "file", "raw_sha256": _sha(raw),
                     "size": len(raw)})
        try:
            text = raw.decode("utf-8")
            normalized = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
            fact["normalized_text_sha256"] = _sha(normalized)
        except UnicodeDecodeError:
            pass
        status = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--", rel)
        fact["git_status"] = status or "CLEAN"
        return fact, None
    except Exception as exc:
        fact.update({"exists": None, "type": "unverifiable", "raw_sha256": None})
        return fact, f"SOURCE_UNREADABLE: {rel}: {exc}"


def observe(root: str | Path, capability: str, *, previous_context_hash: str | None = None) -> dict:
    root = Path(root).resolve()
    declaration, declaration_hash, errors = load_declaration(root)
    if declaration is None:
        raise ValueError("; ".join(errors) or "context declaration not found")
    config = declaration["capabilities"].get(capability)
    if not isinstance(config, dict):
        raise ValueError(f"capability not declared: {capability}")
    try:
        head_before = _git(root, "rev-parse", "HEAD")
    except Exception as exc:
        head_before = None
        errors.append(f"HEAD_UNVERIFIABLE: {exc}")
    facts = []
    for source in config.get("sources", []):
        if not isinstance(source, dict) or "kind" not in source:
            errors.append("INVALID_SOURCE_DECLARATION")
            continue
        try:
            if "path" in source:
                paths = [_safe_relative(source["path"])]
            elif "glob" in source:
                pattern = _safe_relative(source["glob"])
                paths = sorted(p.relative_to(root).as_posix() for p in root.glob(pattern)
                               if p.is_file() or p.is_symlink())
                if not paths and source.get("required", True):
                    errors.append(f"MISSING_REQUIRED_GLOB: {pattern}")
            else:
                raise ValueError("source requires path or glob")
            for rel in paths:
                fact, error = _file_fact(root, rel, source)
                facts.append(fact)
                if error:
                    errors.append(error)
        except Exception as exc:
            errors.append(f"SOURCE_RESOLUTION_FAILED: {exc}")
    try:
        head_after = _git(root, "rev-parse", "HEAD")
    except Exception as exc:
        head_after = None
        errors.append(f"HEAD_UNVERIFIABLE_AFTER: {exc}")
    if head_before != head_after:
        errors.append("WORKSPACE_CHANGED_DURING_SCAN: HEAD")
    try:
        if _sha((root / DECLARATION).read_bytes()) != declaration_hash:
            errors.append("WORKSPACE_CHANGED_DURING_SCAN: DECLARATION")
        for fact in facts:
            if fact.get("exists") is True and fact.get("type") == "file":
                if _sha((root / fact["path"]).read_bytes()) != fact["raw_sha256"]:
                    errors.append(f"WORKSPACE_CHANGED_DURING_SCAN: {fact['path']}")
    except Exception as exc:
        errors.append(f"POST_IDENTITY_UNVERIFIABLE: {exc}")
    observed = {
        "builder_version": BUILDER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "capability": capability,
        "configured_label": config.get("label"),
        "base_git_head": head_after,
        "declaration": {"path": DECLARATION.as_posix(), "raw_sha256": declaration_hash},
        "sources": sorted(facts, key=lambda x: (x["path"], x["kind"])),
        "dependency_edges": sorted({
            f"{('path:' + source['path']) if 'path' in source else ('glob:' + source['glob'])}->{item}"
            for source in config.get("sources", [])
            for item in source.get("context_items", [])
        }),
        "unverifiable_reasons": sorted(set(errors)),
        "scan_identity": {"head_before": head_before, "head_after": head_after,
                          "relevant_sources_reverified": True},
    }
    authoritative = {f["path"]: f["raw_sha256"] for f in facts
                     if f["authority"] == "authoritative"}
    # Hash material contains no timestamp and no prior hash: identical inputs are identical.
    hash_material = {"observed": observed, "approved_semantics": {}}
    manifest_hash = _sha(_canonical(hash_material))
    return {
        "observed": observed,
        "approved_semantics": {},
        "lifecycle": {
            "context_id": f"{capability}:{manifest_hash}",
            "schema_version": SCHEMA_VERSION,
            "manifest_sha256": manifest_hash,
            "previous_context_hash": previous_context_hash,
            "base_git_head": head_after,
            "authoritative_source_hashes": authoritative,
            "generation_provenance": {"builder": BUILDER_VERSION, "mode": "SHADOW"},
        },
    }


def scan(previous: dict | None, current: dict) -> dict:
    reasons, changed = [], []
    errors = current["observed"].get("unverifiable_reasons", [])
    if previous is None:
        if errors:
            status = "UNVERIFIABLE"
        else:
            status = "CONTEXT_UPDATE"
            reasons.append("NO_TRUSTED_BASELINE")
    else:
        try:
            po, co = previous["observed"], current["observed"]
            if errors or po.get("unverifiable_reasons"):
                status = "UNVERIFIABLE"
            else:
                if po["declaration"]["raw_sha256"] != co["declaration"]["raw_sha256"]:
                    reasons.append("DECLARATION_CHANGED")
                old = {(x["path"], x["kind"]): x for x in po["sources"]}
                new = {(x["path"], x["kind"]): x for x in co["sources"]}
                for key in sorted(set(old) | set(new)):
                    before, after = old.get(key), new.get(key)
                    if before == after:
                        continue
                    authority = (after or before).get("authority")
                    if before is None: kind = "ADDED"
                    elif after is None: kind = "REMOVED"
                    elif before.get("raw_sha256") != after.get("raw_sha256"):
                        kind = "EOL_ONLY" if (before.get("normalized_text_sha256") and
                            before.get("normalized_text_sha256") == after.get("normalized_text_sha256")) else "HASH_CHANGED"
                    else: kind = "STATUS_CHANGED"
                    changed.append({"source": key[0], "source_kind": key[1],
                                    "change_kind": kind, "authority": authority,
                                    "affected_context_items": (after or before)["context_items"]})
                authority_change = ("DECLARATION_CHANGED" in reasons or
                                    po.get("dependency_edges") != co.get("dependency_edges") or
                                    any(x["authority"] == "authoritative" for x in changed))
                if po.get("dependency_edges") != co.get("dependency_edges"):
                    reasons.append("DEPENDENCY_EDGES_CHANGED")
                if authority_change: status = "POTENTIAL_AUTHORITY_CHANGE"
                elif changed or po.get("base_git_head") != co.get("base_git_head"):
                    status = "CONTEXT_UPDATE"
                else: status = "NO_IMPACT"
        except Exception as exc:
            status = "UNVERIFIABLE"
            errors = [*errors, f"BASELINE_COMPARISON_FAILED: {exc}"]
    if status == "UNVERIFIABLE":
        reasons.extend(errors or ["UNVERIFIABLE_BASELINE"])
    for item in changed:
        reasons.append(f"{item['change_kind']}: {item['source']}")
    return {"mode": "SHADOW", "delta_status": status,
            "would_block": status in ("POTENTIAL_AUTHORITY_CHANGE", "UNVERIFIABLE"),
            "changed_sources": changed, "reasons": sorted(set(reasons)),
            "unverifiable_reasons": sorted(set(errors))}


def write_shadow_evidence(root: str | Path, capability: str, manifest: dict,
                          report: dict, *, job_id: str) -> tuple[str, str]:
    root = Path(root).resolve()
    declaration, _, _ = load_declaration(root)
    generated = _safe_relative(declaration.get("generated_root", "validation/context"))
    destination = root / generated / capability / job_id
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "context-manifest.json"
    report_path = destination / "delta-report.json"
    manifest_path.write_bytes(_canonical(manifest))
    report_path.write_bytes(_canonical(report))
    return manifest_path.relative_to(root).as_posix(), report_path.relative_to(root).as_posix()


def _baseline_path(cache_root: str | Path, workspace: str, capability: str) -> Path:
    safe = hashlib.sha256(f"{workspace}\0{capability}".encode()).hexdigest()
    return Path(cache_root) / "context-baselines" / f"{safe}.json"


def load_trusted_baseline(cache_root: str | Path, workspace: str,
                          capability: str) -> tuple[dict | None, list[str]]:
    path = _baseline_path(cache_root, workspace, capability)
    if not path.exists():
        return None, []
    try:
        value = json.loads(path.read_text("utf-8"))
        material = {"observed": value["observed"],
                    "approved_semantics": value["approved_semantics"]}
        if value["approved_semantics"] != {}:
            raise ValueError("Phase-1 approved_semantics must be empty")
        if _sha(_canonical(material)) != value["lifecycle"]["manifest_sha256"]:
            raise ValueError("manifest content hash mismatch")
        if value["observed"]["capability"] != capability:
            raise ValueError("capability mismatch")
        return value, []
    except Exception as exc:
        return None, [f"BASELINE_UNTRUSTED: {exc}"]


def begin_shadow(root: str | Path, *, workspace: str, actor: str, mode: str,
                 cache_root: str | Path, evidence_roots: list[dict] | None = None,
                 job=None) -> dict | None:
    declaration, _, declaration_errors = load_declaration(root)
    if declaration is None:
        # Absence means not enabled.  A present but invalid declaration is visible.
        if not (Path(root) / DECLARATION).exists():
            return None
        return {"capability": None, "pre": None, "baseline": None,
                "errors": declaration_errors, "cache_root": str(cache_root)}
    capability = select_capability(declaration, actor=actor, mode=mode)
    if capability is None:
        return None
    baseline, errors = load_trusted_baseline(cache_root, workspace, capability)
    previous_hash = baseline["lifecycle"]["manifest_sha256"] if baseline else None
    try:
        pre = observe(root, capability, previous_context_hash=previous_hash)
    except Exception as exc:
        pre = None
        errors.append(f"PRE_OBSERVATION_FAILED: {exc}")
    session = {"capability": capability, "pre": pre, "baseline": baseline,
               "errors": errors, "cache_root": str(cache_root), "workspace": workspace,
               "evidence_roots": evidence_roots or [], "declaration": declaration}
    session["job"] = job
    return session


def refresh_evidence_index(root: str | Path, session: dict | None) -> dict | None:
    """Generate the comparison-only Phase-2A projection for an active capability."""
    if not session or not session.get("capability"):
        return None
    declaration = session.get("declaration") or {}
    capability = session["capability"]
    config = declaration.get("capabilities", {}).get(capability, {})
    cache_key = hashlib.sha256(
        f"{session['workspace']}\0{capability}".encode("utf-8")).hexdigest()
    return generate_evidence_index(
        root, workspace=session["workspace"], capability=capability,
        source_roots=session.get("evidence_roots", []),
        declared_sources=config.get("sources", []),
        generated_root=declaration.get("generated_root", "validation/context"),
        cache_path=Path(session["cache_root"]) / "evidence-index" / f"{cache_key}.json",
    )


def finish_shadow(root: str | Path, session: dict | None, *, job_id: str) -> dict | None:
    if session is None:
        return None
    capability = session.get("capability")
    errors = list(session.get("errors", []))
    if capability is None or session.get("pre") is None:
        return {"mode": "SHADOW", "capability": capability,
                "manifest_sha256": None, "previous_manifest_sha256": None,
                "delta_status": "UNVERIFIABLE", "would_block": True,
                "report_path": None, "changed_sources": [],
                "unverifiable_reasons": errors or ["CONTEXT_NOT_OBSERVABLE"]}
    try:
        pre = session["pre"]
        current = observe(root, capability,
                          previous_context_hash=pre["lifecycle"]["manifest_sha256"])
        # The mandatory terminal scan detects actor-time mutation.  If none occurred,
        # the prior trusted baseline still determines the externally reported delta.
        execution_delta = scan(pre, current)
        baseline_delta = scan(session.get("baseline"), current)
        rank = {"NO_IMPACT": 0, "CONTEXT_UPDATE": 1,
                "POTENTIAL_AUTHORITY_CHANGE": 2, "UNVERIFIABLE": 3}
        report = execution_delta if rank[execution_delta["delta_status"]] >= rank[baseline_delta["delta_status"]] else baseline_delta
        report = dict(report)
        report["baseline_delta_status"] = baseline_delta["delta_status"]
        report["execution_delta_status"] = execution_delta["delta_status"]
        if errors:
            report["delta_status"] = "UNVERIFIABLE"
            report["would_block"] = True
            report["unverifiable_reasons"] = sorted(set(
                report.get("unverifiable_reasons", []) + errors))
        manifest_path, report_path = write_shadow_evidence(
            root, capability, current, report, job_id=job_id)
        cache = _baseline_path(session["cache_root"], session["workspace"], capability)
        cache.parent.mkdir(parents=True, exist_ok=True)
        temp = cache.with_suffix(".tmp")
        temp.write_bytes(_canonical(current))
        os.replace(temp, cache)
        evidence_index = refresh_evidence_index(root, session)
        job_context = None
        if evidence_index and evidence_index.get("status") == "READY":
            index_rel = _safe_relative(
                f"{session['declaration'].get('generated_root', 'validation/context')}/"
                f"{capability}/evidence-index.json")
            index_value = json.loads((root / index_rel).read_text("utf-8"))
            job = session.get("job")
            if job is not None:
                try:
                    job_context = generate_job_context(
                        root, workspace=session["workspace"], capability=capability, job=job,
                        manifest=current, manifest_path=manifest_path, delta=report,
                        delta_path=report_path, evidence_index=index_value,
                        evidence_index_path=index_rel, declaration=session["declaration"],
                    )
                except Exception as exc:
                    # Comparison diagnostics are deliberately outside the job
                    # qualification domain during Phase 2B.
                    job_context = {
                        "mode": "COMPARISON_ONLY", "status": "UNVERIFIABLE",
                        "schema_version": 1, "sha256": None,
                        "source_context_sha256": current["lifecycle"]["manifest_sha256"],
                        "evidence_index_sha256": evidence_index.get("index_sha256"),
                        "delta_status": report["delta_status"], "authority_ref_count": 0,
                        "evidence_ref_count": 0, "expansion_required_count": 0,
                        "bytes": 0, "report_path": None,
                        "diagnostics": [{"code": "JOB_CONTEXT_BUILD_FAILED",
                                         "detail": str(exc)[:1000]}],
                    }
        return {"mode": "SHADOW", "capability": capability,
                "manifest_sha256": current["lifecycle"]["manifest_sha256"],
                "previous_manifest_sha256": current["lifecycle"]["previous_context_hash"],
                "delta_status": report["delta_status"],
                "would_block": report["would_block"], "manifest_path": manifest_path,
                "report_path": report_path, "changed_sources": report["changed_sources"],
                "unverifiable_reasons": report["unverifiable_reasons"],
                "evidence_index": evidence_index, "job_context": job_context}
    except Exception as exc:
        return {"mode": "SHADOW", "capability": capability,
                "manifest_sha256": None, "previous_manifest_sha256": None,
                "delta_status": "UNVERIFIABLE", "would_block": True,
                "report_path": None, "changed_sources": [],
                "unverifiable_reasons": errors + [f"POST_OBSERVATION_FAILED: {exc}"]}
