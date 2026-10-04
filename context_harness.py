"""Deterministic Phase-1 Capability Context observation and shadow delta scan.

This module deliberately observes bytes and configured relationships only.  It
does not interpret, approve, or inject semantics into an actor prompt.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from attribution_policy import attribution_paths

from evidence_index import generate as generate_evidence_index
from job_context import generate as generate_job_context
from context_materializer import generate as generate_materialized_context
from review_package import generate as generate_review_package
from section_slicing import validate_contract as validate_section_contract


SCHEMA_VERSION = 1
BUILDER_VERSION = "context-harness-phase1-1"
TRUST_SCHEMA_VERSION = 2
DECLARATION = Path(".agent/context.json")
STATES = ("NO_IMPACT", "CONTEXT_UPDATE", "POTENTIAL_AUTHORITY_CHANGE", "UNVERIFIABLE")
SOURCE_ROLES = ("MATERIALIZED_CONTEXT", "STRUCTURED_PROJECTION", "PROVENANCE_SOURCE")


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
        for capability, config in sorted(value["capabilities"].items()):
            if not isinstance(config, dict):
                raise ValueError(f"capability must be an object: {capability}")
            context = config.get("context", {})
            if not isinstance(context, dict):
                raise ValueError(f"context must be an object: {capability}")
            if "max_text_bytes" in context:
                budget = context["max_text_bytes"]
                if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
                    raise ValueError(
                        f"context.max_text_bytes must be a positive integer: {capability}")
            _validate_source_mappings(config, capability)
        return value, _sha(raw), []
    except Exception as exc:
        errors.append(f"DECLARATION_UNREADABLE: {exc}")
        return None, None, errors


def _source_ref(spec: dict) -> str:
    if isinstance(spec.get("path"), str):
        return _safe_relative(spec["path"])
    if isinstance(spec.get("glob"), str):
        return "glob:" + _safe_relative(spec["glob"])
    raise ValueError("source requires exactly one path or glob")


def _validate_source_mappings(config: dict, capability: str = "") -> None:
    sources = config.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError(f"sources must be a list: {capability}")
    refs = []
    for spec in sources:
        if not isinstance(spec, dict) or not isinstance(spec.get("kind"), str):
            raise ValueError(f"invalid source declaration: {capability}")
        if ("path" in spec) == ("glob" in spec):
            raise ValueError(f"source requires exactly one path or glob: {capability}")
        ref = _source_ref(spec)
        if ref in refs:
            raise ValueError(f"duplicate source reference: {ref}")
        refs.append(ref)
        authority = spec.get("authority", "authoritative")
        if authority not in ("authoritative", "non_authority", "observed"):
            raise ValueError(f"invalid authority class: {ref}")
        role = spec.get("source_role", "MATERIALIZED_CONTEXT")
        if role not in SOURCE_ROLES:
            raise ValueError(f"invalid source_role: {ref}")
        provenance = spec.get("projection_provenance")
        if role == "STRUCTURED_PROJECTION":
            if provenance is not None and not isinstance(provenance, list):
                raise ValueError(f"projection_provenance must be a list: {ref}")
            for link in provenance or []:
                if not isinstance(link, dict) or not isinstance(link.get("source_ref"), str):
                    raise ValueError(f"invalid projection provenance link: {ref}")
                _safe_relative(link["source_ref"])
                expected = link.get("expected_sha256")
                version = link.get("expected_version")
                if expected is None and version is None:
                    raise ValueError(f"projection provenance hash or version required: {ref}")
                if expected is not None and (not isinstance(expected, str) or len(expected) != 64):
                    raise ValueError(f"invalid projection provenance hash: {ref}")
                if version is not None and (not isinstance(version, str) or not version):
                    raise ValueError(f"invalid projection provenance version: {ref}")
        elif provenance is not None:
            raise ValueError(f"projection_provenance only allowed for structured projection: {ref}")
        if "always_required" in spec and not isinstance(spec["always_required"], bool):
            raise ValueError(f"always_required must be boolean: {ref}")
        for field in ("context_items", "target_files", "depends_on"):
            values = spec.get(field, [])
            if (not isinstance(values, list) or
                    any(not isinstance(x, str) or not x for x in values)):
                raise ValueError(f"{field} must be a list of non-empty strings: {ref}")
        for target in spec.get("target_files", []):
            _safe_relative(target)
        if (authority == "authoritative" and spec.get("always_required") is False and
                "glob" in spec):
            raise ValueError(f"conditional authority requires a stable path reference: {ref}")
        validate_section_contract(spec, ref)
    known = set(refs)
    roles = {ref: spec.get("source_role", "MATERIALIZED_CONTEXT")
             for spec, ref in zip(sources, refs)}
    referenced = set()
    for spec, ref in zip(sources, refs):
        for dependency in spec.get("depends_on", []):
            normalized = ("glob:" + _safe_relative(dependency[5:])
                          if dependency.startswith("glob:") else _safe_relative(dependency))
            if normalized not in known:
                raise ValueError(f"unknown source dependency: {ref}->{normalized}")
            referenced.add(normalized)
        for link in spec.get("projection_provenance") or []:
            upstream = _safe_relative(link["source_ref"])
            if upstream not in known or roles.get(upstream) != "PROVENANCE_SOURCE":
                raise ValueError(f"projection upstream must be a declared provenance source: {ref}->{upstream}")
    for spec, ref in zip(sources, refs):
        if (spec.get("authority", "authoritative") == "authoritative" and
                spec.get("always_required") is False and
                not spec.get("target_files") and not spec.get("context_items") and
                ref not in referenced):
            raise ValueError(f"conditional authority lacks a structural mapping: {ref}")


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
        "always_required": (bool(spec.get("always_required", True))
                            if spec.get("authority", "authoritative") == "authoritative"
                            else False),
        "target_files": sorted(set(spec.get("target_files", []))),
        "depends_on": sorted(set(spec.get("depends_on", []))),
        "source_role": spec.get("source_role", "MATERIALIZED_CONTEXT"),
    }
    if spec.get("projection_provenance") is not None:
        fact["projection_provenance"] = spec["projection_provenance"]
    if spec.get("sections") is not None:
        fact["section_coverage"] = spec.get("section_coverage")
        fact["sections"] = spec["sections"]
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
        "context_contract": {
            "max_text_bytes": config.get("context", {}).get("max_text_bytes"),
        },
        "base_git_head": head_after,
        "declaration": {"path": DECLARATION.as_posix(), "raw_sha256": declaration_hash},
        "sources": sorted(facts, key=lambda x: (x["path"], x["kind"])),
        "dependency_edges": sorted({
            f"{('path:' + source['path']) if 'path' in source else ('glob:' + source['glob'])}->{item}"
            for source in config.get("sources", [])
            for item in source.get("context_items", [])
        }),
        "source_dependency_edges": sorted({
            f"{_source_ref(source)}->{dependency}"
            for source in config.get("sources", [])
            for dependency in source.get("depends_on", [])
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


def _candidate_path(cache_root: str | Path, workspace: str, capability: str) -> Path:
    safe = hashlib.sha256(f"{workspace}\0{capability}".encode()).hexdigest()
    return Path(cache_root) / "context-candidates" / f"{safe}.json"


def _receipt_path(cache_root: str | Path, workspace: str, capability: str,
                  manifest_sha256: str) -> Path:
    safe = hashlib.sha256(f"{workspace}\0{capability}".encode()).hexdigest()
    return (Path(cache_root) / "context-acceptance-receipts" / safe /
            f"{manifest_sha256}.json")


def _atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=path.name + ".",
                                             suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _promotion_decision(*, baseline: dict | None, report: dict,
                        evidence_index: dict | None, job_context: dict | None,
                        materialized_context: dict | None,
                        review_package: dict | None,
                        candidate_already_known: bool = False) -> tuple[bool, str]:
    """Compatibility diagnostic: observation is never an acceptance operation."""
    delta = report.get("delta_status")
    if report.get("would_block") or delta in ("POTENTIAL_AUTHORITY_CHANGE", "UNVERIFIABLE"):
        return False, "RECONCILIATION_OR_VERIFICATION_REQUIRED"
    if not evidence_index or evidence_index.get("status") != "READY":
        return False, "EVIDENCE_INDEX_NOT_READY"
    if job_context is not None and job_context.get("status") not in ("READY_BOUNDED", "READY"):
        return False, "JOB_CONTEXT_NOT_READY"
    if materialized_context is not None and materialized_context.get("status") not in (
            "READY_BOUNDED", "READY"):
        return False, "MATERIALIZED_CONTEXT_NOT_READY"
    if review_package is not None and review_package.get("status") not in (
            "READY_PACKAGE", "REUSED"):
        return False, "REVIEW_PACKAGE_NOT_READY"
    return False, "EXPLICIT_ACCEPTANCE_REQUIRED"


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
        provenance = value.get("lifecycle", {}).get("acceptance_provenance")
        if not isinstance(provenance, dict):
            raise ValueError("legacy baseline has no acceptance provenance")
        if provenance.get("schema_version") != TRUST_SCHEMA_VERSION:
            raise ValueError("unsupported acceptance provenance schema")
        receipt_path = _receipt_path(cache_root, workspace, capability,
                                     value["lifecycle"]["manifest_sha256"])
        if not receipt_path.is_file():
            raise ValueError("acceptance receipt missing")
        receipt_raw = receipt_path.read_bytes()
        if _sha(receipt_raw) != provenance.get("receipt_sha256"):
            raise ValueError("acceptance receipt hash mismatch")
        receipt = json.loads(receipt_raw.decode("utf-8"))
        domain = receipt.get("trust_domain", {})
        accepted = receipt.get("accepted_candidate", {})
        if (receipt.get("schema_version") != TRUST_SCHEMA_VERSION or
                receipt.get("acceptance_type") not in
                (None, "HUMAN_EXPLICIT", "LEGACY_AUTO_MIGRATION") or
                domain != {"workspace": workspace, "capability": capability} or
                accepted.get("manifest_sha256") != value["lifecycle"]["manifest_sha256"] or
                accepted.get("declaration_sha256") !=
                value["observed"]["declaration"]["raw_sha256"] or
                provenance.get("receipt_identity") != receipt.get("receipt_identity")):
            raise ValueError("acceptance receipt does not bind this baseline")
        identity_material = dict(receipt)
        identity = identity_material.pop("receipt_identity", None)
        if identity != _sha(_canonical(identity_material)):
            raise ValueError("acceptance receipt identity mismatch")
        return value, []
    except Exception as exc:
        return None, [f"BASELINE_UNTRUSTED: {exc}"]


def _load_legacy_baseline(cache_root: str | Path, workspace: str,
                          capability: str) -> dict | None:
    """Load hash-valid pre-receipt state solely as migration comparison material."""
    path = _baseline_path(cache_root, workspace, capability)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text("utf-8"))
        if value.get("lifecycle", {}).get("acceptance_provenance") is not None:
            return None
        material = {"observed": value["observed"],
                    "approved_semantics": value["approved_semantics"]}
        if (value["approved_semantics"] == {} and
                value["observed"]["capability"] == capability and
                _sha(_canonical(material)) == value["lifecycle"]["manifest_sha256"]):
            return value
    except Exception:
        pass
    return None


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
    legacy_baseline = (_load_legacy_baseline(cache_root, workspace, capability)
                       if baseline is None else None)
    if legacy_baseline is not None:
        errors = [error for error in errors
                  if "legacy baseline has no acceptance provenance" not in error]
    comparison_baseline = baseline or legacy_baseline
    previous_hash = (comparison_baseline["lifecycle"]["manifest_sha256"]
                     if comparison_baseline else None)
    try:
        pre = observe(root, capability, previous_context_hash=previous_hash)
    except Exception as exc:
        pre = None
        errors.append(f"PRE_OBSERVATION_FAILED: {exc}")
    session = {"capability": capability, "pre": pre, "baseline": baseline,
               "comparison_baseline": comparison_baseline,
               "legacy_baseline": legacy_baseline is not None,
               "errors": errors, "cache_root": str(cache_root), "workspace": workspace,
               "evidence_roots": evidence_roots or [], "declaration": declaration}
    session["candidate_already_known"] = _candidate_path(
        cache_root, workspace, capability).exists()
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


def finish_shadow(root: str | Path, session: dict | None, *, job_id: str,
                  before: dict | None = None, after: dict | None = None,
                  attributable_changed_paths: list[str] | None = None) -> dict | None:
    if session is None:
        return None
    capability = session.get("capability")
    declaration = session.get("declaration") or {}
    config = declaration.get("capabilities", {}).get(capability, {})
    errors = list(session.get("errors", []))
    if capability is None or session.get("pre") is None:
        return {"mode": "SHADOW", "capability": capability,
                "manifest_sha256": None, "previous_manifest_sha256": None,
                "delta_status": "UNVERIFIABLE", "would_block": True,
                "report_path": None, "changed_sources": [],
                "unverifiable_reasons": errors or ["CONTEXT_NOT_OBSERVABLE"]}
    try:
        artifact_phase = ("post-actor-validation"
                          if session.get("pre_actor_input") is not None
                          else "pre-actor")
        pre = session["pre"]
        current = observe(root, capability,
                          previous_context_hash=pre["lifecycle"]["manifest_sha256"])
        # The mandatory terminal scan detects actor-time mutation.  If none occurred,
        # the prior trusted baseline still determines the externally reported delta.
        execution_delta = scan(pre, current)
        baseline_delta = scan(session.get("comparison_baseline"), current)
        rank = {"NO_IMPACT": 0, "CONTEXT_UPDATE": 1,
                "POTENTIAL_AUTHORITY_CHANGE": 2, "UNVERIFIABLE": 3}
        report = execution_delta if rank[execution_delta["delta_status"]] >= rank[baseline_delta["delta_status"]] else baseline_delta
        report = dict(report)
        report["baseline_delta_status"] = baseline_delta["delta_status"]
        report["execution_delta_status"] = execution_delta["delta_status"]
        if session.get("legacy_baseline"):
            report["delta_status"] = "POTENTIAL_AUTHORITY_CHANGE"
            report["would_block"] = True
            report["reasons"] = sorted(set(
                report.get("reasons", []) + ["LEGACY_BASELINE_UNPROVEN"]))
        if errors:
            report["delta_status"] = "UNVERIFIABLE"
            report["would_block"] = True
            report["unverifiable_reasons"] = sorted(set(
                report.get("unverifiable_reasons", []) + errors))
        manifest_path, report_path = write_shadow_evidence(
            root, capability, current, report, job_id=job_id)
        evidence_index = refresh_evidence_index(root, session)
        job_context = None
        materialized_context = None
        review_package = None
        if evidence_index and evidence_index.get("status") == "READY":
            index_rel = _safe_relative(
                f"{session['declaration'].get('generated_root', 'validation/context')}/"
                f"{capability}/evidence-index.json")
            index_value = json.loads((root / index_rel).read_text("utf-8"))
            job = session.get("job")
            explicit_package = (job is not None and
                getattr(job, "review_mode", None) == "DELTA_REVIEW" and
                getattr(job, "review_package_ref", None) is not None)
            if job is not None and not explicit_package:
                try:
                    job_context = generate_job_context(
                        root, workspace=session["workspace"], capability=capability, job=job,
                        manifest=current, manifest_path=manifest_path, delta=report,
                        delta_path=report_path, evidence_index=index_value,
                        evidence_index_path=index_rel, declaration=session["declaration"],
                        artifact_phase=artifact_phase,
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
                if job_context and job_context.get("job_context_path"):
                    job_context_value = json.loads(
                        (root / job_context["job_context_path"]).read_text("utf-8"))
                    try:
                        materialized_context = generate_materialized_context(
                            root, generated_root=session["declaration"].get(
                                "generated_root", "validation/context"),
                            workspace=session["workspace"], capability=capability,
                            job_context=job_context_value,
                            job_context_path=job_context["job_context_path"],
                            evidence_index=index_value, evidence_index_path=index_rel,
                            artifact_phase=artifact_phase)
                    except Exception as exc:
                        materialized_context = {
                            "mode": "COMPARISON_ONLY", "status": "UNVERIFIABLE",
                            "schema_version": 1, "sha256": None,
                            "source_context_sha256": current["lifecycle"]["manifest_sha256"],
                            "evidence_index_sha256": evidence_index.get("index_sha256"),
                            "item_count": 0, "bytes": 0, "payload_bytes": 0,
                            "report_path": None,
                            "diagnostics": [{"code": "MATERIALIZED_CONTEXT_BUILD_FAILED",
                                             "detail": str(exc)[:1000]}],
                        }
                    try:
                        request = (getattr(job, "instruction_ref", None) or {}).get(
                            "context_request", {})
                        requested_targets = (request.get("target_files", [])
                                             if isinstance(request, dict) else [])
                        targets = list(requested_targets) if isinstance(
                            requested_targets, list) else []
                        sources = []
                        for spec in config.get("sources", []):
                            if isinstance(spec, dict):
                                sources.extend(spec.get(key) for key in ("path", "glob")
                                               if isinstance(spec.get(key), str))
                                targets.extend(path for path in spec.get("target_files", [])
                                               if isinstance(path, str))
                                for section in spec.get("sections", []):
                                    if isinstance(section, dict):
                                        targets.extend(
                                            path for path in section.get("target_files", [])
                                            if isinstance(path, str))
                        attributable = attribution_paths(
                            attributable_changed_paths or [],
                            generated_roots=[_safe_relative(
                                f"{session['declaration'].get('generated_root', 'validation/context')}/{capability}")],
                            protected_paths=list(targets) + sources)
                        review_package = generate_review_package(
                            root, generated_root=session["declaration"].get(
                                "generated_root", "validation/context"),
                            workspace=session["workspace"], capability=capability, job=job,
                            manifest=current, manifest_path=manifest_path, delta=report,
                            delta_path=report_path, evidence_index=index_value,
                            evidence_index_path=index_rel, job_context=job_context_value,
                            job_context_path=job_context["job_context_path"], before=before,
                            after=after, changed_paths=attributable,
                        )
                    except Exception as exc:
                        review_package = {
                            "mode": "COMPARISON_ONLY", "candidate_review_mode": None,
                            "status": "UNVERIFIABLE", "schema_version": 1,
                            "sha256": None, "source_context_sha256": current["lifecycle"]["manifest_sha256"],
                            "job_context_sha256": job_context.get("sha256"),
                            "evidence_index_sha256": evidence_index.get("index_sha256"),
                            "file_count": 0, "bytes": 0, "ref_count": 0, "finding_count": 0,
                            "expansion_required_count": 0,
                            "attribution_status": "ATTRIBUTION_UNCERTAIN", "report_path": None,
                            "diagnostics": [{"code": "REVIEW_PACKAGE_BUILD_FAILED",
                                             "detail": str(exc)[:1000]}],
                        }
            elif explicit_package:
                review_package = {
                    "mode": "SUPPLIED_PACKAGE", "candidate_review_mode": "DELTA_REVIEW",
                    "status": "REUSED", "schema_version": 1,
                    "sha256": job.review_package_ref.get("sha256"),
                    "package_path": job.review_package_ref.get("path"),
                    "package_reused": True, "package_regenerated": False,
                    "diagnostic_only": False,
                }
        promote, promotion_reason = _promotion_decision(
            baseline=session.get("baseline"), report=report,
            evidence_index=evidence_index, job_context=job_context,
            materialized_context=materialized_context, review_package=review_package,
            candidate_already_known=session.get("candidate_already_known", False))
        candidate = _candidate_path(session["cache_root"], session["workspace"], capability)
        # Observation only ever records a candidate.  Trust changes are performed
        # by context_trust.accept_candidate after explicit operator authorization.
        _atomic_write(candidate, {
                "schema_version": TRUST_SCHEMA_VERSION,
                "trust_domain": {"workspace": session["workspace"],
                                 "capability": capability},
                "declaration_sha256": current["observed"]["declaration"]["raw_sha256"],
                "candidate_kind": ("LEGACY_MIGRATION" if session.get("legacy_baseline")
                                   else "RECONCILIATION"),
                "reconciliation_eligible": bool(
                    report.get("delta_status") != "UNVERIFIABLE" and
                    evidence_index and evidence_index.get("status") == "READY" and
                    (job_context is None or job_context.get("status") in
                     ("READY_BOUNDED", "READY")) and
                    (materialized_context is None or materialized_context.get("status") in
                     ("READY_BOUNDED", "READY"))),
                "trusted_baseline_sha256": (
                    (session.get("comparison_baseline") or {}).get("lifecycle", {}).get(
                        "manifest_sha256")),
                "candidate_manifest_sha256": current["lifecycle"]["manifest_sha256"],
                "candidate": current,
                "delta": report,
                "promotion_blocked_reason": promotion_reason,
            })
        result = {"mode": "SHADOW", "capability": capability,
                "snapshot": ("POST_ACTOR_VALIDATION" if artifact_phase ==
                             "post-actor-validation" else "PRE_ACTOR_INPUT"),
                "manifest_sha256": current["lifecycle"]["manifest_sha256"],
                "previous_manifest_sha256": current["lifecycle"]["previous_context_hash"],
                "delta_status": report["delta_status"],
                "would_block": report["would_block"], "manifest_path": manifest_path,
                "report_path": report_path, "changed_sources": report["changed_sources"],
                "unverifiable_reasons": report["unverifiable_reasons"],
                "evidence_index": evidence_index, "job_context": job_context,
                "materialized_context": materialized_context,
                "review_package": review_package}
        result["trust_state"] = {
            "trusted_baseline_sha256": (
                (session.get("baseline") or {}).get("lifecycle", {}).get(
                    "manifest_sha256")),
            "observed_candidate_sha256": current["lifecycle"]["manifest_sha256"],
            "baseline_promoted": False,
            "promotion_reason": promotion_reason,
            "candidate_path": str(candidate),
        }
        if session.get("pre_actor_input") is not None:
            prior = session["pre_actor_input"]
            result["snapshots"] = {
                "pre_actor_input": {
                    "manifest_sha256": prior.get("manifest_sha256"),
                    "job_context_sha256": (prior.get("job_context") or {}).get("sha256"),
                    "materialized_context_sha256": (
                        prior.get("materialized_context") or {}).get(
                            "materialized_context_sha256"),
                },
                "post_actor_validation": {
                    "manifest_sha256": result.get("manifest_sha256"),
                    "delta_status": result.get("delta_status"),
                    "job_context_sha256": (result.get("job_context") or {}).get("sha256"),
                    "job_context_path": (result.get("job_context") or {}).get(
                        "job_context_path"),
                    "materialized_context_sha256": (
                        result.get("materialized_context") or {}).get(
                            "materialized_context_sha256"),
                    "materialized_context_path": (
                        result.get("materialized_context") or {}).get(
                            "materialized_context_path"),
                },
            }
        return result
    except Exception as exc:
        return {"mode": "SHADOW", "capability": capability,
                "manifest_sha256": None, "previous_manifest_sha256": None,
                "delta_status": "UNVERIFIABLE", "would_block": True,
                "report_path": None, "changed_sources": [],
                "unverifiable_reasons": errors + [f"POST_OBSERVATION_FAILED: {exc}"]}
