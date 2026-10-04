"""Explicit, operator-controlled Context Harness trust acceptance."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

import context_harness as harness


TOOL_VERSION = "context-trust-1"


class AcceptanceError(ValueError):
    """A deterministic refusal to change trust state."""


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AcceptanceError(f"invalid JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AcceptanceError(f"JSON object required at {path}")
    return value


def _write_exclusive(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = harness._canonical(value)
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise AcceptanceError(f"immutable audit record already exists: {path}") from exc


def _manifest_identity(value: dict) -> str:
    material = {"observed": value["observed"],
                "approved_semantics": value["approved_semantics"]}
    actual = harness._sha(harness._canonical(material))
    if actual != value.get("lifecycle", {}).get("manifest_sha256"):
        raise AcceptanceError("candidate manifest content hash mismatch")
    return actual


def _raw_baseline(path: Path, workspace: str, capability: str) -> tuple[dict | None, str | None]:
    if not path.exists():
        return None, None
    value = _read_json(path)
    identity = _manifest_identity(value)
    if value.get("observed", {}).get("capability") != capability:
        raise AcceptanceError("current baseline capability mismatch")
    return value, identity


def _accept_candidate_locked(*, root: str | Path, cache_root: str | Path, workspace: str,
                     capability: str, expected_candidate_sha256: str,
                     operator: str, reason: str,
                     expected_current_trusted_sha256: str | None = None,
                     now: dt.datetime | None = None) -> dict:
    """Accept exactly the persisted candidate after re-observing its sources."""
    if not workspace or not capability or not operator.strip() or not reason.strip():
        raise AcceptanceError("workspace, capability, operator, and reason are required")
    if len(expected_candidate_sha256) != 64:
        raise AcceptanceError("expected candidate hash must be a SHA-256 hex digest")
    root, cache_root = Path(root).resolve(), Path(cache_root).resolve()
    candidate_path = harness._candidate_path(cache_root, workspace, capability)
    baseline_path = harness._baseline_path(cache_root, workspace, capability)
    candidate_record = _read_json(candidate_path)
    if candidate_record.get("schema_version") != harness.TRUST_SCHEMA_VERSION:
        raise AcceptanceError("unsupported candidate schema")
    if candidate_record.get("trust_domain") != {"workspace": workspace,
                                                  "capability": capability}:
        raise AcceptanceError("candidate trust domain mismatch")
    candidate = candidate_record.get("candidate")
    if not isinstance(candidate, dict):
        raise AcceptanceError("candidate evidence missing")
    candidate_hash = _manifest_identity(candidate)
    if candidate_hash != expected_candidate_sha256:
        raise AcceptanceError("expected candidate hash mismatch")
    if candidate_record.get("candidate_manifest_sha256") != candidate_hash:
        raise AcceptanceError("candidate envelope identity mismatch")
    delta = candidate_record.get("delta", {})
    if candidate_record.get("reconciliation_eligible") is not True:
        raise AcceptanceError("candidate lacks completed reconciliation evidence")
    eligible = delta.get("delta_status") in ("CONTEXT_UPDATE", "POTENTIAL_AUTHORITY_CHANGE")
    if candidate_record.get("candidate_kind") == "LEGACY_MIGRATION" and \
            delta.get("delta_status") == "NO_IMPACT":
        eligible = True
    if not eligible:
        raise AcceptanceError("candidate is not eligible for reconciliation")
    declaration_hash = candidate.get("observed", {}).get("declaration", {}).get("raw_sha256")
    if candidate_record.get("declaration_sha256") != declaration_hash:
        raise AcceptanceError("candidate declaration identity mismatch")

    current = harness.observe(root, capability,
                              previous_context_hash=candidate.get("lifecycle", {}).get(
                                  "previous_context_hash"))
    if _manifest_identity(current) != candidate_hash:
        raise AcceptanceError("candidate is stale relative to current observable state")

    old, old_hash = _raw_baseline(baseline_path, workspace, capability)
    recorded_old = candidate_record.get("trusted_baseline_sha256")
    if recorded_old != old_hash:
        raise AcceptanceError("candidate no longer matches current trusted baseline")
    if (expected_current_trusted_sha256 is not None and
            expected_current_trusted_sha256 != old_hash):
        raise AcceptanceError("expected current trusted hash mismatch")

    timestamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(
        dt.timezone.utc).isoformat().replace("+00:00", "Z")
    domain_key = hashlib.sha256(f"{workspace}\0{capability}".encode()).hexdigest()
    accepted_candidate_path = (cache_root / "context-accepted-candidates" / domain_key /
                               f"{candidate_hash}.json")
    if accepted_candidate_path.exists():
        if accepted_candidate_path.read_bytes() != harness._canonical(candidate_record):
            raise AcceptanceError("existing accepted-candidate identity collision")
    else:
        _write_exclusive(accepted_candidate_path, candidate_record)
    archive_rel = None
    if old is not None:
        archive_path = (cache_root / "context-baseline-archive" / domain_key /
                        f"{old_hash}.json")
        if archive_path.exists():
            if archive_path.read_bytes() != harness._canonical(old):
                raise AcceptanceError("existing baseline archive identity collision")
        else:
            _write_exclusive(archive_path, old)
        archive_rel = archive_path.relative_to(cache_root).as_posix()

    receipt = {
        "schema_version": harness.TRUST_SCHEMA_VERSION,
        "trust_domain": {"workspace": workspace, "capability": capability},
        "previous_trusted": {"manifest_sha256": old_hash,
                             "archive_path": archive_rel},
        "accepted_candidate": {
            "manifest_sha256": candidate_hash,
            "declaration_sha256": declaration_hash,
            "base_git_head": candidate.get("observed", {}).get("base_git_head"),
            "candidate_path": accepted_candidate_path.relative_to(cache_root).as_posix(),
        },
        "operator": operator.strip(), "reason": reason.strip(),
        "accepted_at": timestamp, "tool_version": TOOL_VERSION,
    }
    receipt["receipt_identity"] = harness._sha(harness._canonical(receipt))
    receipt_path = harness._receipt_path(cache_root, workspace, capability, candidate_hash)
    if receipt_path.exists():
        existing = _read_json(receipt_path)
        if (old_hash == candidate_hash and
                existing.get("accepted_candidate", {}).get("manifest_sha256") == candidate_hash):
            trusted, errors = harness.load_trusted_baseline(cache_root, workspace, capability)
            if trusted is not None and not errors:
                return {"status": "ALREADY_ACCEPTED", "workspace": workspace,
                        "capability": capability, "trusted_manifest_sha256": candidate_hash,
                        "receipt_identity": existing.get("receipt_identity")}
        raise AcceptanceError("acceptance receipt already exists")
    _write_exclusive(receipt_path, receipt)

    promoted = json.loads(harness._canonical(candidate).decode("utf-8"))
    promoted["lifecycle"]["acceptance_provenance"] = {
        "schema_version": harness.TRUST_SCHEMA_VERSION,
        "receipt_identity": receipt["receipt_identity"],
        "receipt_sha256": harness._sha(receipt_path.read_bytes()),
    }
    harness._atomic_write(baseline_path, promoted)
    trusted, errors = harness.load_trusted_baseline(cache_root, workspace, capability)
    if trusted is None or errors:
        raise AcceptanceError("post-acceptance provenance validation failed: " + "; ".join(errors))
    return {"status": "ACCEPTED", "workspace": workspace, "capability": capability,
            "previous_trusted_manifest_sha256": old_hash,
            "trusted_manifest_sha256": candidate_hash,
            "receipt_identity": receipt["receipt_identity"],
            "receipt_path": receipt_path.relative_to(cache_root).as_posix(),
            "archive_path": archive_rel}


def accept_candidate(*, root: str | Path, cache_root: str | Path, workspace: str,
                     capability: str, expected_candidate_sha256: str,
                     operator: str, reason: str,
                     expected_current_trusted_sha256: str | None = None,
                     now: dt.datetime | None = None) -> dict:
    """Serialize compare-and-swap acceptance within one trust domain."""
    cache = Path(cache_root).resolve()
    domain_key = hashlib.sha256(f"{workspace}\0{capability}".encode()).hexdigest()
    lock_path = cache / "context-trust-locks" / f"{domain_key}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise AcceptanceError("trust domain acceptance is already in progress") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        return _accept_candidate_locked(
            root=root, cache_root=cache, workspace=workspace, capability=capability,
            expected_candidate_sha256=expected_candidate_sha256, operator=operator,
            reason=reason,
            expected_current_trusted_sha256=expected_current_trusted_sha256, now=now)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accept", nargs="?")
    parser.add_argument("--root", required=True)
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--capability", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--expected-current-trusted-sha256")
    args = parser.parse_args(argv)
    if args.accept != "accept":
        parser.error("the 'accept' operation is required")
    try:
        result = accept_candidate(
            root=args.root, cache_root=args.cache_root, workspace=args.workspace,
            capability=args.capability, expected_candidate_sha256=args.candidate_sha256,
            operator=args.operator, reason=args.reason,
            expected_current_trusted_sha256=args.expected_current_trusted_sha256)
    except AcceptanceError as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
