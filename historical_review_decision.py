"""Explicit one-time normalization of a known historical actor review result."""
from __future__ import annotations
import hashlib, json, os, shutil, tempfile
from pathlib import Path

from review_decision import validate
from review_evidence import (_assert_safe_existing_parents, _is_reparse,
                             _package_matches, _safe_relative, _tree_snapshot)

def _canonical(v): return (json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
def _sha(b): return hashlib.sha256(b).hexdigest()

def adopt_historical_review_decision(*, canonical, evidence_root, source_job_id,
                                     workspace, log_dir, mapping_path, human_approved):
    """Install only an explicitly supplied mapping tied to exact local source bytes."""
    result = {"status":"FAILED", "mode":"HISTORICAL_MANUAL", "source_job_id":source_job_id,
              "destination":None, "manifest_sha256":None, "trust":"ACTOR_REPORTED"}
    staging = None
    try:
        if not human_approved: raise ValueError("explicit --human-approved is required")
        root = Path(os.path.abspath(canonical)); relroot = _safe_relative(evidence_root, label="review_evidence_root")
        jobrel = _safe_relative(source_job_id, label="source_job_id")
        destination = root / relroot / jobrel
        result["destination"] = destination.relative_to(root).as_posix()
        _assert_safe_existing_parents(root, destination)
        if destination.exists(): raise ValueError("historical review destination already exists")
        source = Path(log_dir) / "review-execution.json"
        if not source.is_file() or source.is_symlink(): raise ValueError("source review execution missing")
        source_bytes = source.read_bytes(); execution = json.loads(source_bytes)
        if execution.get("exit_code") != 0 or not isinstance(execution.get("final_result_text"), str):
            raise ValueError("source is not a completed review result")
        mapping = json.loads(Path(mapping_path).read_text("utf-8"))
        decision = validate(mapping, review_job_id=source_job_id, workspace=workspace)
        # This is an explicit consistency guard, not extraction: supplied IDs must occur
        # literally in the exact actor result selected by the approver.
        absent = [f["finding_id"] for f in decision["findings"]
                  if f["finding_id"] not in execution["final_result_text"]]
        if absent: raise ValueError("mapped finding IDs absent from exact source: " + ",".join(absent))
        decision["provenance"] = {**decision["provenance"],
            "normalization_mode":"HISTORICAL_MANUAL", "source_job_id":source_job_id,
            "source_file":"review-execution.json", "source_sha256":_sha(source_bytes),
            "human_approved":True}
        decision_bytes = _canonical(decision)
        manifest = {"schema":"worker-review-evidence", "version":1, "job_id":source_job_id,
            "actor":decision["actor"], "mode":"review", "workspace":decision["workspace"],
            "actor_status":"DONE", "review_boundary":"HISTORICAL_UNKNOWN",
            "adoption":{"mode":"HISTORICAL_MANUAL","human_approved":True},
            "normalized_files":[{"path":"review-decision.json","sha256":_sha(decision_bytes)}],
            "review_decision":{"path":"review-decision.json","sha256":_sha(decision_bytes),
                "finding_count":len(decision["findings"]),"trust":"ACTOR_REPORTED"},
            "source_provenance":{"source_job_id":source_job_id,"source_sha256":_sha(source_bytes),
                "source_storage":"LOCAL_ONLY_NOT_COPIED"},
            "raw_local_only":[{"path":"review-execution.json","sha256":_sha(source_bytes),"storage":"LOCAL_ONLY"}],
            "trust_limitation":"Human-approved normalization preserves actor judgment; Worker validates bytes and schema, not substantive correctness."}
        mb = _canonical(manifest); package={"review-decision.json":decision_bytes,"review-manifest.json":mb}
        before=_tree_snapshot(root,destination); destination.parent.mkdir(parents=True,exist_ok=True)
        staging=Path(tempfile.mkdtemp(prefix=".historical-review-",dir=destination.parent))
        for n,b in package.items(): (staging/n).write_bytes(b)
        os.replace(staging,destination); staging=None
        if before != _tree_snapshot(root,destination):
            shutil.rmtree(destination,ignore_errors=True); raise ValueError("canonical content changed outside destination")
        result.update(status="ADOPTED",manifest_sha256=_sha(mb),source_sha256=_sha(source_bytes),
                      finding_count=len(decision["findings"]))
    except Exception as exc: result["error"]=str(exc)[:4000]
    finally:
        if staging: shutil.rmtree(staging,ignore_errors=True)
    return result
