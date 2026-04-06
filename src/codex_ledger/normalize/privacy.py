from __future__ import annotations

from collections.abc import Mapping

from codex_ledger.domain.records import RedactionMode, WorkspaceRecord

DEFAULT_REDACTION_MODE: RedactionMode = "redacted"


def render_workspace_label(
    workspace: WorkspaceRecord,
    *,
    mode: RedactionMode = DEFAULT_REDACTION_MODE,
    aliases: Mapping[str, str] | None = None,
) -> str:
    if mode == "full":
        return workspace.resolved_root_path
    if mode == "alias":
        if aliases is None:
            return workspace.redacted_label
        return aliases.get(workspace.workspace_key, workspace.redacted_label)
    return workspace.redacted_label
