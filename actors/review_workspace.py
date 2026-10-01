import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path


class ReviewPreparationError(RuntimeError):
    def __init__(self, failure_class, message):
        super().__init__(message)
        self.failure_class = failure_class


def _git(root, *args, text=False, check=True):
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True,
        text=text, encoding="utf-8" if text else None,
        errors="replace" if text else None, timeout=60,
    )
    if check and result.returncode:
        error = result.stderr if text else result.stderr.decode("utf-8", "replace")
        raise RuntimeError(error.strip() or f"git {' '.join(args)} failed")
    return result


def listed_input_paths(root):
    raw = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    paths = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        rel = item.decode("utf-8", "surrogateescape").replace("\\", "/")
        path = Path(root, *rel.split("/"))
        # A staged deletion is listed by --cached but has no current raw bytes.
        if path.is_file() and not path.is_symlink():
            paths.append(rel)
    return sorted(set(paths), key=lambda value: value.encode("utf-8", "surrogateescape"))


def build_input_manifest(root):
    root = Path(root)
    files = []
    for rel in listed_input_paths(root):
        data = (root / Path(*rel.split("/"))).read_bytes()
        files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    body = {"version": 1, "files": files}
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")).encode("utf-8", "surrogatepass")
    return {
        **body,
        "file_count": len(files),
        "input_manifest_sha256": hashlib.sha256(serialized).hexdigest(),
    }


def capture_canonical_state(root):
    manifest = build_input_manifest(root)
    index = _git(root, "ls-files", "--stage", "-z").stdout
    return {
        "head": _git(root, "rev-parse", "HEAD", text=True).stdout.strip(),
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "input_manifest_sha256": manifest["input_manifest_sha256"],
        "manifest": manifest,
    }


def _copy_inputs(source, destination, manifest):
    wanted = {entry["path"] for entry in manifest["files"]}
    tracked = _git(destination, "ls-files", "-z").stdout
    for raw in tracked.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape").replace("\\", "/")
        target = destination / Path(*rel.split("/"))
        if rel not in wanted and (target.exists() or target.is_symlink()):
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
    for entry in manifest["files"]:
        rel_path = Path(*entry["path"].split("/"))
        target = destination / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / rel_path, target)


@dataclass
class ReviewWorkspace:
    canonical: Path
    root: Path
    canonical_before: dict
    input_manifest: dict
    head_before: str
    settings_path: Path
    canonical_diff_stat: str
    review_diff_stat: str


def create_review_workspace(canonical, job_id):
    canonical = Path(canonical).resolve()
    base = None
    try:
        before = capture_canonical_state(canonical)
    except Exception as exc:
        raise ReviewPreparationError("REVIEW_SNAPSHOT_FAILED", str(exc)) from exc
    try:
        # A sibling is outside the canonical tree even when the canonical repo
        # itself is the worker repository.
        base = Path(tempfile.mkdtemp(prefix=f"chatgpt-review-{job_id}-", dir=canonical.parent))
        root = base / "workspace"
        clone = subprocess.run(["git", "clone", "--no-hardlinks", "--no-checkout",
                                str(canonical), str(root)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120)
        if clone.returncode:
            raise RuntimeError(clone.stderr.strip() or "git clone failed")
        subprocess.run(["git", "-C", str(root), "checkout", "--detach", before["head"]],
                       check=True, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
        _copy_inputs(canonical, root, before["manifest"])
        actual = build_input_manifest(root)
        if actual != before["manifest"]:
            raise ReviewPreparationError("REVIEW_SNAPSHOT_MISMATCH", "review input manifest differs")
        metadata = base / "worker-metadata"
        metadata.mkdir()
        settings = metadata / "claude-settings.json"
        settings.write_text(json.dumps({
            "hooks": {},
            # --tools exposes Bash; dontAsk additionally requires an explicit
            # approval or the invocation is denied instead of prompting.
            "permissions": {"allow": ["Bash"]},
        }, separators=(",", ":")), encoding="utf-8")
        return ReviewWorkspace(
            canonical, root, before, before["manifest"],
            _git(root, "rev-parse", "HEAD", text=True).stdout.strip(), settings,
            _git(canonical, "diff", "HEAD", "--stat", text=True, check=False).stdout,
            _git(root, "diff", "HEAD", "--stat", text=True, check=False).stdout,
        )
    except ReviewPreparationError:
        if base is not None:
            shutil.rmtree(base, ignore_errors=True)
        raise
    except Exception as exc:
        if base is not None:
            shutil.rmtree(base, ignore_errors=True)
        raise ReviewPreparationError("REVIEW_WORKSPACE_CREATE_FAILED", str(exc)) from exc


def verify_original_inputs(review):
    for entry in review.input_manifest["files"]:
        path = review.root / Path(*entry["path"].split("/"))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            return False
    return True


def scan_outputs(review):
    original = {entry["path"] for entry in review.input_manifest["files"]}
    outputs = []
    for path in review.root.rglob("*"):
        if not path.is_file() or ".git" in path.relative_to(review.root).parts:
            continue
        rel = path.relative_to(review.root).as_posix()
        if rel not in original:
            outputs.append(rel)
    return sorted(outputs)


def finish_review(review):
    head_after = _git(review.root, "rev-parse", "HEAD", text=True, check=False).stdout.strip()
    inputs_clean = verify_original_inputs(review)
    canonical_after = capture_canonical_state(review.canonical)
    canonical_clean = all(canonical_after[key] == review.canonical_before[key]
                          for key in ("head", "index_sha256", "input_manifest_sha256"))
    boundary = "CANONICAL_STATE_CHANGED" if not canonical_clean else (
        "CLEAN" if inputs_clean else "INPUT_MODIFIED")
    return boundary, canonical_after, head_after, scan_outputs(review)


def diff_head_stat(root):
    return _git(root, "diff", "HEAD", "--stat", text=True, check=False).stdout


def cleanup_review(review, timeout=10, outcome_path=None):
    outcome = {"status": "PENDING"}

    def remove():
        try:
            def clear_readonly(function, path, exc):
                try:
                    os.chmod(path, 0o700)
                    function(path)
                except OSError:
                    raise exc

            if sys.version_info >= (3, 12):
                shutil.rmtree(review.root.parent, onexc=clear_readonly)
            else:
                # Compatibility for supported Python versions before 3.12.
                def clear_readonly_legacy(function, path, exc_info):
                    clear_readonly(function, path, exc_info[1])
                shutil.rmtree(review.root.parent, onerror=clear_readonly_legacy)
            outcome["status"] = "DONE"
        except Exception:
            outcome["status"] = "FAILED"
        if outcome_path is not None:
            Path(outcome_path).write_text(
                json.dumps({"status": outcome["status"]}, separators=(",", ":")),
                encoding="utf-8",
            )

    thread = threading.Thread(target=remove, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    return outcome["status"] if not thread.is_alive() else "PENDING"
