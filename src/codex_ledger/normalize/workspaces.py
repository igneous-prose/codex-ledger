from __future__ import annotations

from pathlib import PurePath

from codex_ledger.domain.records import WorkspaceRecord
from codex_ledger.utils.hashing import sha256_text


def resolve_workspace(raw_cwd: str | None, session_cwd: str | None) -> WorkspaceRecord:
    if raw_cwd:
        return _workspace_from_path(raw_cwd, "turn_context.cwd")
    if session_cwd:
        return _workspace_from_path(session_cwd, "session_meta.cwd")
    return WorkspaceRecord(
        workspace_key="workspace-unknown",
        resolved_root_path="unknown",
        resolved_root_path_hash=sha256_text("unknown"),
        display_label="unknown",
        redacted_display_label="unknown",
        resolution_strategy="unknown",
    )


def _workspace_from_path(path_value: str, strategy: str) -> WorkspaceRecord:
    path_hash = sha256_text(path_value)
    name = PurePath(path_value).name or path_value
    return WorkspaceRecord(
        workspace_key=f"workspace-{path_hash[:16]}",
        resolved_root_path=path_value,
        resolved_root_path_hash=path_hash,
        display_label=name,
        redacted_display_label=f"workspace-{path_hash[:8]}",
        resolution_strategy=strategy,
    )
