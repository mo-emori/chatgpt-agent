"""Worker-owned transport of normalized Claude review evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path, PureWindowsPath


NORMALIZED_FILES = (
    "review-input.json",
    "review-execution.json",
    "canonical-diff-head-before.stat",
    "canonical-diff-head-after.stat",
    "review-diff-head-before.stat",
    "review-diff-head-after.stat",
)
RAW_LOCAL_ONLY_FILES = ("claude-stream.jsonl", "stderr.txt")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class ReviewEvidenceError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative(value: str, *, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ReviewEvidenceError(f"invalid {label}")
    windows = PureWindowsPath(value)
    if windows.is_absolute() or windows.drive or value.startswith(("\\\\", "//")):
        raise ReviewEvidenceError(f"{label} must be relative")
    if ":" in value:
        raise ReviewEvidenceError(f"{label} contains an ADS/drive separator")
    parts = value.replace("\\", "/").split("/")
    if any(not part or part in (".", "..") for part in parts):
        raise ReviewEvidenceError(f"{label} contains traversal or an empty component")
    for part in parts:
        stem = part.rstrip(" .").split(".", 1)[0].upper()
        if part != part.rstrip(" .") or stem in _RESERVED:
            raise ReviewEvidenceError(f"{label} contains a reserved Windows component")
    return Path(*parts)


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attrs = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attrs & reparse)


def _assert_safe_existing_parents(canonical: Path, target: Path) -> None:
    current = canonical
    if _is_reparse(current):
        raise ReviewEvidenceError("canonical workspace is a symlink/reparse point")
    for part in target.relative_to(canonical).parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if _is_reparse(current):
                raise ReviewEvidenceError("evidence destination has a symlink/reparse parent")


def _tree_snapshot(root: Path, excluded: Path) -> dict[str, tuple]:
    """Snapshot canonical content outside the configured evidence root."""
    result = {}
    excluded_rel = excluded.relative_to(root)
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root)
        dirs[:] = sorted(d for d in dirs if d != ".git")
        if rel_dir == Path(".") and ".git" in dirs:
            dirs.remove(".git")
        kept = []
        for name in dirs:
            rel = rel_dir / name
            if rel == excluded_rel or excluded_rel in rel.parents:
                continue
            path = current_path / name
            if _is_reparse(path):
                result[rel.as_posix()] = ("link", os.readlink(path))
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            path = current_path / name
            rel = path.relative_to(root)
            if rel == excluded_rel or excluded_rel in rel.parents:
                continue
            if _is_reparse(path):
                result[rel.as_posix()] = ("link", os.readlink(path))
            else:
                data = path.read_bytes()
                result[rel.as_posix()] = ("file", len(data), _sha256(data))
    return result


def _base_result(status: str, destination=None, error=None) -> dict:
    value = {
        "status": status,
        "mode": "LIVE",
        "destination": destination,
        "manifest_sha256": None,
        "normalized_files": [],
        "raw_local_only": [],
    }
    if error is not None:
        value["error"] = str(error)[:4000]
    return value


def not_run_review_evidence(configured: bool) -> dict:
    return _base_result("NOT_RUN" if configured else "NOT_CONFIGURED")


def failed_review_evidence(error) -> dict:
    return _base_result("FAILED", error=error)


def _package_matches(destination: Path, files: dict[str, bytes]) -> bool:
    try:
        entries = list(destination.iterdir())
        if any(not p.is_file() or p.is_symlink() for p in entries):
            return False
        existing = {p.name for p in entries}
        if existing != set(files):
            return False
        return all((destination / name).read_bytes() == data for name, data in files.items())
    except OSError:
        return False


def _validate_normalized(name: str, data: bytes):
    if name not in ("review-input.json", "review-execution.json"):
        return None
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewEvidenceError(f"corrupt normalized evidence: {name}") from exc
    if not isinstance(value, dict):
        raise ReviewEvidenceError(f"corrupt normalized evidence: {name}")
    if name == "review-input.json" and not {
        "input_manifest_sha256", "file_count", "files"
    }.issubset(value):
        raise ReviewEvidenceError(f"corrupt normalized evidence: {name}")
    if name == "review-execution.json" and not {
        "events", "final_result_text", "exit_code"
    }.issubset(value):
        raise ReviewEvidenceError(f"corrupt normalized evidence: {name}")
    return value


def adopt_review_evidence(*, canonical, review_evidence_root, job_id, log_dir,
                          actor, mode, canonical_head, review_head_before,
                          review_head_after, input_manifest_sha256, file_count,
                          review_boundary, actor_status, evidence_persisted,
                          adoptable, review_context=None) -> dict:
    """Atomically adopt a deterministic, normalized LIVE evidence package."""
    if not review_evidence_root:
        return not_run_review_evidence(False)
    if not adoptable:
        return not_run_review_evidence(True)

    destination_rel = None
    staging = None
    installed = False
    normalized = []
    raw = []
    try:
        # Path.resolve() uses a Windows handle operation that is unavailable in
        # some restricted Worker sandboxes.  Absolute lexical normalization is
        # sufficient here because every existing component is separately
        # rejected when it is a symlink/reparse point.
        canonical = Path(os.path.abspath(canonical))
        if not canonical.is_dir():
            raise ReviewEvidenceError("canonical workspace does not exist")
        root_rel = _safe_relative(review_evidence_root, label="review_evidence_root")
        job_rel = _safe_relative(job_id, label="job_id")
        root = canonical / root_rel
        destination = root / job_rel
        destination_rel = destination.relative_to(canonical).as_posix()
        if root == canonical or canonical not in root.parents or root not in destination.parents:
            raise ReviewEvidenceError("evidence destination escaped canonical workspace")
        _assert_safe_existing_parents(canonical, destination)

        log_dir = Path(log_dir)
        package = {}
        for name in RAW_LOCAL_ONLY_FILES:
            source = log_dir / name
            if source.is_file() and not source.is_symlink():
                raw.append({"path": name, "sha256": _sha256(source.read_bytes()),
                            "storage": "LOCAL_ONLY"})
        for name in NORMALIZED_FILES:
            source = log_dir / name
            if not source.is_file() or source.is_symlink():
                raise ReviewEvidenceError(f"required normalized evidence missing: {name}")
            data = source.read_bytes()
            parsed = _validate_normalized(name, data)
            if name == "review-input.json" and (
                parsed["input_manifest_sha256"] != input_manifest_sha256
                or parsed["file_count"] != file_count
            ):
                raise ReviewEvidenceError("review-input.json provenance mismatch")
            package[name] = data
            normalized.append({"path": name, "sha256": _sha256(data)})
        manifest = {
            "schema": "worker-review-evidence",
            "version": 1,
            "job_id": job_id,
            "actor": actor,
            "mode": mode,
            "canonical_head": canonical_head,
            "review": {"head_before": review_head_before, "head_after": review_head_after},
            "input_manifest_sha256": input_manifest_sha256,
            "file_count": file_count,
            "review_boundary": review_boundary,
            "actor_status": actor_status,
            "evidence_persisted": bool(evidence_persisted),
            "adoptable": bool(adoptable),
            "review_context": review_context,
            "adoption": {"mode": "LIVE"},
            "normalized_files": normalized,
            "raw_local_only": raw,
            "source_provenance": {
                "owner": "Local Agent Worker",
                "source": "Worker-owned local job log evidence",
            },
        }
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")) + "\n").encode("utf-8")
        package["review-manifest.json"] = manifest_bytes
        manifest_hash = _sha256(manifest_bytes)

        before = _tree_snapshot(canonical, destination)
        root.mkdir(parents=True, exist_ok=True)
        _assert_safe_existing_parents(canonical, destination)
        if destination.exists():
            if not destination.is_dir() or _is_reparse(destination) or not _package_matches(destination, package):
                raise ReviewEvidenceError("evidence destination already contains a different package")
            status = "NOOP"
        else:
            staging = Path(tempfile.mkdtemp(prefix=".review-evidence-", dir=root))
            for name, data in package.items():
                (staging / name).write_bytes(data)
            os.replace(staging, destination)
            staging = None
            installed = True
            status = "ADOPTED"
        after = _tree_snapshot(canonical, destination)
        if before != after:
            if installed:
                shutil.rmtree(destination, ignore_errors=True)
            raise ReviewEvidenceError("canonical content changed outside the review evidence root during adoption")
        result = _base_result(status, destination_rel)
        result.update({"manifest_sha256": manifest_hash,
                       "normalized_files": normalized, "raw_local_only": raw})
        return result
    except Exception as exc:
        result = _base_result("FAILED", destination_rel, exc)
        result.update({"normalized_files": normalized, "raw_local_only": raw})
        return result
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
