"""Deterministic Phase-3A Review Delta Package builder and validator.

The package is comparison-only.  It is neither an actor input nor a review
qualification gate, and it never derives semantic facts from prose.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path, PurePosixPath


SCHEMA = "context-harness-review-package"
SCHEMA_VERSION = 1
BUILDER_VERSION = "context-harness-phase3a-1"
MODES = ("DELTA_REVIEW", "BOUNDARY_REVIEW", "FULL_REVIEW")
STATUSES = ("READY_PACKAGE", "NEEDS_RECONCILIATION", "UNVERIFIABLE")
PACKAGE_FILES = (
    "job-context.json", "delta.json", "diff.patch", "findings.json",
    "authority-refs.json", "validation-results.json", "evidence-refs.json",
    "expansion-plan.json", "comparison.json",
)


def canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or ":" in value:
        raise ValueError(f"unsafe workspace-relative path: {value!r}")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(x in ("", ".", "..") for x in path.parts):
        raise ValueError(f"unsafe workspace-relative path: {value!r}")
    return path.as_posix()


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0)
                                     & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def safe_path(root: str | Path, rel: str, *, require_file: bool = True) -> Path:
    root = Path(root).resolve()
    rel = safe_relative(rel)
    current = root
    for part in PurePosixPath(rel).parts:
        current /= part
        if (current.exists() or current.is_symlink()) and _is_link(current):
            raise ValueError(f"symlink/reparse escape rejected: {rel}")
    if root not in current.parents:
        raise ValueError(f"path escaped workspace: {rel}")
    if require_file and not current.is_file():
        raise ValueError(f"package file missing: {rel}")
    return current


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            timeout=30)
    if result.returncode:
        raise OSError(result.stderr.decode("utf-8", "replace").strip() or "git failed")
    return result.stdout


def _mode(job) -> str:
    ref = getattr(job, "instruction_ref", None) or {}
    request = ref.get("context_request", {}) if isinstance(ref, dict) else {}
    value = request.get("review_mode", "DELTA_REVIEW") if isinstance(request, dict) else "DELTA_REVIEW"
    if value not in MODES:
        raise ValueError(f"unsupported review mode: {value!r}")
    return value


def _attributed_patch(root: Path, before: dict | None, after: dict | None,
                      changed_paths: list[str] | None) -> tuple[bytes, str, list[str], list[dict]]:
    """Return a patch only when the Worker snapshots prove safe attribution."""
    expansions = []
    if before is None or after is None or changed_paths is None:
        return b"", "ATTRIBUTION_UNCERTAIN", [], [{
            "missing_ref": "worker_git_before_after", "reason": "WORKER_SNAPSHOT_UNAVAILABLE",
            "authority": "WORKER_OBSERVED", "quality": "MISSING",
            "required_before_review": True, "suggested_scope": [],
        }]
    paths = sorted({safe_relative(x) for x in changed_paths})
    if before.get("head") != after.get("head"):
        return b"", "ATTRIBUTION_UNCERTAIN", paths, [{
            "missing_ref": "exact_base_head_transition", "reason": "HEAD_CHANGED_DURING_JOB",
            "authority": "WORKER_OBSERVED", "quality": "PARTIAL",
            "required_before_review": True, "suggested_scope": paths,
        }]
    before_states = before.get("path_states", {})
    unsafe = [p for p in paths if p in before_states]
    if unsafe:
        return b"", "ATTRIBUTION_UNCERTAIN", paths, [{
            "missing_ref": "clean_per_path_baseline", "reason": "TARGET_PATH_DIRTY_BEFORE_JOB",
            "authority": "WORKER_OBSERVED", "quality": "PARTIAL",
            "required_before_review": True, "suggested_scope": unsafe,
        }]
    if not paths:
        return b"", "EXACT", [], []
    # A path absent from the before dirty-state map was identical to HEAD.  The
    # current HEAD-to-worktree patch is therefore the exact job delta.  --binary
    # makes binary changes deterministic and reviewable.
    patch = _git(root, "diff", "--binary", "--no-ext-diff", "HEAD", "--", *paths)
    # Untracked additions are absent from git diff; encode them as no-index patches.
    after_states = after.get("path_states", {})
    for rel in paths:
        if after_states.get(rel, {}).get("status") == "??":
            target = safe_path(root, rel)
            result = subprocess.run(["git", "-C", str(root), "diff", "--binary",
                                     "--no-index", "--", os.devnull, rel],
                                    capture_output=True, timeout=30)
            if result.returncode not in (0, 1):
                raise OSError(result.stderr.decode("utf-8", "replace"))
            patch += result.stdout
    return patch, "EXACT", paths, expansions


def _expansion(item: dict) -> dict:
    return {
        "missing_ref": item.get("source_ref") or item.get("evidence_ref"),
        "reason": item.get("reason", "UNSPECIFIED_EXPANSION"),
        "authority": item.get("authority_class", "evidence"),
        "quality": item.get("quality", "PARTIAL"),
        "required_before_review": bool(item.get(
            "required_before_review", item.get("required_before_package_first_execution", False))),
        "suggested_scope": sorted(set(item.get("suggested_scope", []))),
    }


def build(root: str | Path, *, workspace: str, capability: str, job,
          manifest: dict, manifest_path: str, delta: dict, delta_path: str,
          evidence_index: dict, evidence_index_path: str, job_context: dict,
          job_context_path: str, before: dict | None = None, after: dict | None = None,
          changed_paths: list[str] | None = None) -> tuple[dict[str, bytes], dict]:
    root = Path(root).resolve()
    mode = _mode(job)
    # Reverify canonical inputs and their declared identities before selection.
    input_facts = {}
    for name, rel in (("context_manifest", manifest_path), ("delta_report", delta_path),
                      ("evidence_index", evidence_index_path), ("job_context", job_context_path)):
        raw = safe_path(root, rel).read_bytes()
        input_facts[name] = {"path": safe_relative(rel), "sha256": sha256(raw), "size": len(raw)}
        parsed = json.loads(raw.decode("utf-8"))
        expected = {"context_manifest": manifest, "delta_report": delta,
                    "evidence_index": evidence_index, "job_context": job_context}[name]
        if parsed != expected:
            raise ValueError(f"stale substituted input: {name}")
    context_material = {"observed": manifest["observed"],
                        "approved_semantics": manifest["approved_semantics"]}
    if sha256(canonical(context_material)) != manifest["lifecycle"]["manifest_sha256"]:
        raise ValueError("context manifest identity mismatch")
    if evidence_index.get("workspace") != workspace or evidence_index.get("capability") != capability:
        raise ValueError("evidence index workspace/capability mismatch")
    if job_context.get("workspace") != workspace or job_context.get("capability") != capability:
        raise ValueError("job context workspace/capability mismatch")
    if job_context.get("manifest_sha256") != job_context.get("package_sha256"):
        raise ValueError("job context identity mismatch")
    jc_material = dict(job_context); jc_hash = jc_material.pop("manifest_sha256", None); jc_material.pop("package_sha256", None)
    if sha256(canonical(jc_material)) != jc_hash:
        raise ValueError("job context content hash mismatch")

    patch, attribution, attributed, attribution_expansion = _attributed_patch(
        root, before, after, changed_paths)
    refs = job_context.get("evidence_refs", [])
    requested = set(job_context.get("requested_finding_ids", []) +
                    job_context.get("previous_finding_ids", []))
    findings = []
    for ref in refs:
        if ref.get("quality") == "STRUCTURED" and isinstance(ref.get("findings"), list):
            for finding in ref["findings"]:
                if not requested or finding.get("finding_id") in requested:
                    findings.append({**finding, "evidence_id": ref.get("evidence_id"),
                                     "provenance": "ACTOR_REPORTED_STRUCTURED"})
    findings = sorted(findings, key=lambda x: (x.get("finding_id", ""), x.get("evidence_id", "")))
    authority = list(job_context.get("authoritative_source_refs", []))
    boundary = []
    if mode == "BOUNDARY_REVIEW":
        boundary = sorted(job_context.get("dependency_states", []),
                          key=lambda x: (x.get("context_item", ""), x.get("source_ref", "")))
    expansions = [_expansion(x) for x in job_context.get("expansion_requirements", [])]
    expansions.extend(attribution_expansion)
    if mode == "FULL_REVIEW":
        expansions.append({"missing_ref": "full_repository", "reason": "FULL_REVIEW_REQUIRES_BROAD_REPO_VISIBILITY",
                           "authority": "review_mode", "quality": "DECLARED",
                           "required_before_review": True, "suggested_scope": []})
    for ref in refs:
        if ref.get("quality") in ("PARTIAL", "UNSTRUCTURED"):
            expansions.append({"missing_ref": ref.get("evidence_id"),
                               "reason": "PRIOR_FINDINGS_NOT_STRUCTURALLY_AVAILABLE",
                               "authority": "evidence", "quality": ref.get("quality"),
                               "required_before_review": True,
                               "suggested_scope": [ref.get("evidence_path")] if ref.get("evidence_path") else []})
    expansions = sorted({canonical(x): x for x in expansions}.values(), key=canonical)

    validation = []
    for ref in refs:
        if ref.get("evidence_type") == "historical_job":
            validation.append({"evidence_id": ref.get("evidence_id"),
                               "job_id": ref.get("job_id"), "job_status": ref.get("job_status"),
                               "failure_class": ref.get("failure_class"),
                               "authority": "WORKER_OBSERVED"})
        elif ref.get("evidence_type") == "review":
            validation.append({"evidence_id": ref.get("evidence_id"),
                               "job_id": ref.get("job_id"),
                               "actor_execution_status": ref.get("actor_execution_status"),
                               "review_verdict": ref.get("review_verdict"),
                               "execution_authority": "WORKER_OBSERVED",
                               "verdict_authority": "ACTOR_REPORTED"})
    status = ("UNVERIFIABLE" if job_context.get("selection_status") == "UNVERIFIABLE"
              else "NEEDS_RECONCILIATION" if attribution != "EXACT" or expansions or
              job_context.get("selection_status") == "NEEDS_RECONCILIATION"
              else "READY_PACKAGE")
    included_refs = []
    for item in authority:
        rel = safe_relative(item["source_ref"])
        raw = safe_path(root, rel).read_bytes()
        if item.get("raw_sha256") != sha256(raw):
            raise ValueError(f"selected authority ref changed: {rel}")
        included_refs.append({"path": rel, "raw_sha256": sha256(raw), "size": len(raw),
                              "role": "authority"})
    selected_ids = {x.get("evidence_id") for x in refs}
    for entry in evidence_index.get("entries", []):
        if entry.get("evidence_id") not in selected_ids:
            continue
        for item in entry.get("source_files", []):
            rel = safe_relative(item["path"])
            raw = safe_path(root, rel).read_bytes()
            if item.get("sha256") != sha256(raw):
                raise ValueError(f"selected evidence ref changed: {rel}")
            included_refs.append({"path": rel, "raw_sha256": sha256(raw), "size": len(raw),
                                  "role": "evidence"})
    included_refs = sorted({(x["path"], x["role"]): x for x in included_refs}.values(),
                           key=lambda x: (x["path"], x["role"]))
    payload = {
        "job-context.json": canonical(job_context),
        "delta.json": canonical({"base_git_head": before.get("head") if before else None,
                                  "head_after": after.get("head") if after else None,
                                  "worker_observed_changed_paths": attributed,
                                  "attribution_status": attribution,
                                  "delta_report_status": delta.get("delta_status")}),
        "diff.patch": patch,
        "findings.json": canonical({"findings": findings, "finding_count": len(findings),
                                     "selection": "STRUCTURED_ONLY"}),
        "authority-refs.json": canonical({"selected_authority_refs": authority,
                                           "declared_boundary_refs": boundary}),
        "validation-results.json": canonical({"results": sorted(validation, key=canonical),
                                                "claims_are_not_execution_facts": True}),
        "evidence-refs.json": canonical({"evidence_refs": refs}),
        "expansion-plan.json": canonical({"requirements": expansions,
                                           "automatic_full_repo_fallback": False}),
        "comparison.json": canonical({
            "basis": "STRUCTURALLY_INDEXED_PRIOR_REVIEW_NEEDS_ONLY",
            "included_relevant_material": {
                "finding_ids": [x["finding_id"] for x in findings],
                "changed_paths": attributed,
                "authority_ref_count": len(authority)},
            "omitted_relevant_material": [x["missing_ref"] for x in expansions if x["required_before_review"]],
            "over_included_material": [], "required_expansion_count": len(expansions),
            "prior_actor_usage": None,
        }),
    }
    file_facts = [{"path": name, "raw_sha256": sha256(payload[name]), "size": len(payload[name])}
                  for name in PACKAGE_FILES]
    manifest_material = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "builder_version": BUILDER_VERSION,
        "workspace": workspace, "capability": capability, "mode": "COMPARISON_ONLY",
        "candidate_review_mode": mode, "status": status,
        "source_context_manifest_sha256": manifest["lifecycle"]["manifest_sha256"],
        "source_context_file_sha256": input_facts["context_manifest"]["sha256"],
        "delta_report_sha256": input_facts["delta_report"]["sha256"],
        "delta_report_status": delta.get("delta_status"),
        "evidence_index_sha256": input_facts["evidence_index"]["sha256"],
        "job_context_sha256": input_facts["job_context"]["sha256"],
        "job_context_status": job_context.get("selection_status"),
        "target": {"job_id": getattr(job, "job_id", None),
                   "instruction_sha256": getattr(job, "prompt_sha256", None)},
        "git": {"base": before.get("head") if before else None,
                "head": after.get("head") if after else None,
                "worker_observed_changed_paths": attributed,
                "attribution_status": attribution},
        "source_inputs": input_facts,
        "refs": included_refs,
        "files": file_facts, "package_file_count": len(file_facts),
        "package_byte_count": sum(x["size"] for x in file_facts),
        "previous": {"context_sha256": manifest["lifecycle"].get("previous_context_hash"),
                     "package_sha256": None},
        "generation_provenance": {"builder": BUILDER_VERSION, "owner": "Local Agent Worker",
                                  "actor_content_executed": False},
        "integrity_status": "VERIFIED_AT_GENERATION",
    }
    package_hash = sha256(canonical(manifest_material))
    manifest = dict(manifest_material, package_sha256=package_hash,
                    manifest_sha256=package_hash)
    payload["package-manifest.json"] = canonical(manifest)
    report = {"mode": "COMPARISON_ONLY", "candidate_review_mode": mode, "status": status,
              "schema_version": SCHEMA_VERSION, "sha256": package_hash,
              "source_context_sha256": manifest_material["source_context_manifest_sha256"],
              "job_context_sha256": manifest_material["job_context_sha256"],
              "evidence_index_sha256": manifest_material["evidence_index_sha256"],
              "file_count": len(file_facts), "bytes": manifest_material["package_byte_count"],
              "ref_count": len(included_refs),
              "finding_count": len(findings), "expansion_required_count": len(expansions),
              "attribution_status": attribution}
    return payload, report


def generate(root: str | Path, *, generated_root: str, **kwargs) -> dict:
    root = Path(root).resolve()
    rel = safe_relative(f"{generated_root}/{kwargs['capability']}/{getattr(kwargs['job'], 'job_id')}/review-package")
    destination = safe_path(root, rel, require_file=False)
    destination.mkdir(parents=True, exist_ok=True)
    payload, report = build(root, **kwargs)
    for name in sorted(payload):
        (destination / name).write_bytes(payload[name])
    report_path = destination.parent / "review-package-report.json"
    report["report_path"] = report_path.relative_to(root).as_posix()
    report["package_path"] = destination.relative_to(root).as_posix()
    report_path.write_bytes(canonical(report))
    return report


def validate_ref(root: str | Path, ref: dict, *, workspace: str, capability: str,
                 current_context_sha256: str | None = None,
                 current_job_context_sha256: str | None = None,
                 target_job_id: str | None = None) -> dict:
    root = Path(root).resolve()
    package_rel = safe_relative(ref["path"])
    manifest_path = safe_path(root, f"{package_rel}/package-manifest.json")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported package schema")
    if manifest.get("workspace") != workspace or manifest.get("capability") != capability:
        raise ValueError("package workspace/capability mismatch")
    material = dict(manifest); claimed = material.pop("manifest_sha256", None); material.pop("package_sha256", None)
    actual = sha256(canonical(material))
    if actual != claimed or actual != ref.get("sha256"):
        raise ValueError("package manifest/hash mismatch")
    expected_target = target_job_id or ref.get("target_job_id")
    if expected_target is not None and manifest.get("target", {}).get("job_id") != expected_target:
        raise ValueError("package target job identity mismatch")
    for fact in manifest.get("files", []):
        rel = safe_relative(fact["path"])
        raw = safe_path(root, f"{package_rel}/{rel}").read_bytes()
        if len(raw) != fact["size"] or sha256(raw) != fact["raw_sha256"]:
            raise ValueError(f"package file tampered: {rel}")
    for fact in manifest.get("refs", []):
        rel = safe_relative(fact["path"])
        raw = safe_path(root, rel).read_bytes()
        if len(raw) != fact["size"] or sha256(raw) != fact["raw_sha256"]:
            raise ValueError(f"package ref stale or tampered: {rel}")
    for name, fact in manifest.get("source_inputs", {}).items():
        rel = safe_relative(fact["path"])
        raw = safe_path(root, rel).read_bytes()
        if len(raw) != fact["size"] or sha256(raw) != fact["sha256"]:
            raise ValueError(f"package source input stale or tampered: {name}")
    if current_context_sha256 is not None and manifest.get("source_context_manifest_sha256") != current_context_sha256:
        raise ValueError("stale source Context Manifest SHA")
    if current_job_context_sha256 is not None and manifest.get("job_context_sha256") != current_job_context_sha256:
        raise ValueError("stale Job Context SHA")
    return manifest
