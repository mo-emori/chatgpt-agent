"""Generic path policy for Local-Agent generated infrastructure output."""
from __future__ import annotations

from pathlib import PurePosixPath


def normalize_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or ":" in value:
        raise ValueError(f"unsafe workspace-relative path: {value!r}")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe workspace-relative path: {value!r}")
    return path.as_posix()


def _literal_prefix(value: str) -> str:
    """Return the path prefix before a declaration's first glob metacharacter."""
    parts = PurePosixPath(normalize_path(value)).parts
    literal = []
    for part in parts:
        if any(char in part for char in "*?["):
            break
        literal.append(part)
    return PurePosixPath(*literal).as_posix() if literal else ""


def paths_overlap(left: str, right: str) -> bool:
    """Conservatively detect equality or ancestor overlap, including globs."""
    left_prefix, right_prefix = _literal_prefix(left), _literal_prefix(right)
    if not left_prefix or not right_prefix:
        return True
    left_parts = PurePosixPath(left_prefix).parts
    right_parts = PurePosixPath(right_prefix).parts
    length = min(len(left_parts), len(right_parts))
    return left_parts[:length] == right_parts[:length]


def attribution_paths(paths: list[str], *, generated_roots: list[str],
                      protected_paths: list[str]) -> list[str]:
    """Exclude known infrastructure output without masking requested inputs.

    Protected paths are declared targets, sources, or authorities.  Any overlap
    is configuration ambiguity and fails closed instead of being filtered.
    """
    roots = sorted({normalize_path(root) for root in generated_roots})
    protected = sorted({normalize_path(path) for path in protected_paths})
    overlaps = [(root, path) for root in roots for path in protected
                if paths_overlap(root, path)]
    if overlaps:
        detail = ", ".join(f"{root}<->{path}" for root, path in overlaps)
        raise ValueError(f"generated infrastructure root overlaps protected path: {detail}")
    result = []
    for value in paths:
        path = normalize_path(value)
        if any(paths_overlap(root, path) for root in roots):
            continue
        result.append(path)
    return sorted(set(result))
