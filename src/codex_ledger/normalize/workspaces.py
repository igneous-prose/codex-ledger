from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from codex_ledger.domain.records import WorkspaceRecord
from codex_ledger.utils.hashing import sha256_text

DEFAULT_ROOT_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
)


def resolve_workspace(
    raw_cwd: str | None,
    session_cwd: str | None,
    *,
    root_markers: Iterable[str] = DEFAULT_ROOT_MARKERS,
) -> WorkspaceRecord:
    observed_cwd = raw_cwd or session_cwd
    if observed_cwd is None:
        return WorkspaceRecord(
            workspace_key="workspace-unknown",
            raw_cwd=None,
            resolved_root_path="unknown",
            resolved_root_path_hash=sha256_text("unknown"),
            display_label="unknown",
            redacted_display_label="unknown",
            resolution_strategy="unknown",
        )

    markers = tuple(_clean_markers(root_markers))
    resolved_path, strategy = _resolve_root_from_path(observed_cwd, markers)
    path_hash = sha256_text(resolved_path)
    display_label = Path(resolved_path).name or resolved_path
    return WorkspaceRecord(
        workspace_key=f"workspace-{path_hash[:16]}",
        raw_cwd=observed_cwd,
        resolved_root_path=resolved_path,
        resolved_root_path_hash=path_hash,
        display_label=display_label,
        redacted_display_label=f"workspace-{path_hash[:8]}",
        resolution_strategy=strategy,
    )


def _resolve_root_from_path(path_value: str, root_markers: tuple[str, ...]) -> tuple[str, str]:
    candidate = Path(path_value).expanduser()
    if not candidate.is_absolute():
        return (path_value, "raw_cwd")

    candidate = candidate.resolve(strict=False)
    marker_root = _find_root_with_markers(candidate, root_markers)
    if marker_root is not None:
        return (str(marker_root), "project_root_marker")

    git_root = _find_git_root(candidate)
    if git_root is not None:
        return (str(git_root), "git_root")

    return (str(candidate), "raw_cwd")


def _find_root_with_markers(path: Path, root_markers: tuple[str, ...]) -> Path | None:
    if not root_markers:
        return None

    for current in (path, *path.parents):
        if any((current / marker).exists() for marker in root_markers):
            return current
    return None


def _find_git_root(path: Path) -> Path | None:
    for current in (path, *path.parents):
        if (current / ".git").exists():
            return current
    return None


def _clean_markers(root_markers: Iterable[str]) -> tuple[str, ...]:
    return tuple(marker for marker in root_markers if marker)
