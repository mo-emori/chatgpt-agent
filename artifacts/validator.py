import re
from pathlib import Path


class ArtifactPathError(ValueError):
    pass


RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _is_relative_to(
    path: Path,
    root: Path,
) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_artifact_path(
    raw_path: str,
    *,
    workspace_root: Path,
    artifact_roots: list[str],
) -> Path:
    raw = raw_path.strip()

    if not raw:
        raise ArtifactPathError(
            "Empty artifact path"
        )

    # UNC / extended Windows path
    if raw.startswith("\\\\"):
        raise ArtifactPathError(
            "UNC/extended path not allowed"
        )

    # C:\foo / C:foo
    if re.match(
        r"^[A-Za-z]:",
        raw,
    ):
        raise ArtifactPathError(
            "Drive-qualified path not allowed"
        )

    path = Path(raw)

    if path.is_absolute():
        raise ArtifactPathError(
            "Absolute path not allowed"
        )

    # Windows ADS / colon
    if ":" in raw:
        raise ArtifactPathError(
            "Colon/ADS not allowed"
        )

    for part in path.parts:
        normalized = part.rstrip(" .")

        # trailing dot / space
        if normalized != part:
            raise ArtifactPathError(
                "Trailing dot/space not allowed"
            )

        stem = normalized.split(".")[0].upper()

        if stem in RESERVED_NAMES:
            raise ArtifactPathError(
                f"Reserved Windows name: {part}"
            )

    workspace_root = (
        workspace_root.resolve()
    )

    resolved = (
        workspace_root / path
    ).resolve()

    if not _is_relative_to(
        resolved,
        workspace_root,
    ):
        raise ArtifactPathError(
            "Artifact escapes workspace"
        )

    if artifact_roots:
        allowed = False

        for root_name in artifact_roots:
            allowed_root = (
                workspace_root
                / root_name
            ).resolve()

            if _is_relative_to(
                resolved,
                allowed_root,
            ):
                allowed = True
                break

        if not allowed:
            raise ArtifactPathError(
                "Artifact outside allowed roots"
            )

    if not resolved.is_file():
        raise ArtifactPathError(
            f"Artifact not found: {raw}"
        )

    return resolved