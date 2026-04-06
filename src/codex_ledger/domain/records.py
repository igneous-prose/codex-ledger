from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SourceKind = Literal[
    "local_rollout_file",
    "imported_json_report",
    "stdout_json_capture",
    "cloud_task_json",
    "reconciliation_reference",
]

SUPPORTED_SOURCE_KINDS: tuple[SourceKind, ...] = (
    "local_rollout_file",
    "imported_json_report",
    "stdout_json_capture",
    "cloud_task_json",
    "reconciliation_reference",
)

IMPLEMENTED_SOURCE_KINDS: tuple[SourceKind, ...] = (
    "local_rollout_file",
    "imported_json_report",
)

RedactionMode = Literal["redacted", "alias", "full"]


@dataclass(frozen=True)
class ImportCandidate:
    source_path: Path
    source_kind: SourceKind


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace_key: str
    raw_cwd: str | None
    resolved_root_path: str
    resolved_root_path_hash: str
    display_label: str
    redacted_display_label: str
    resolution_strategy: str

    @property
    def redacted_label(self) -> str:
        return self.redacted_display_label


@dataclass(frozen=True)
class ProviderSessionRecord:
    session_key: str
    raw_session_id: str
    session_meta_json: str
    session_started_at_utc: str | None
    session_ended_at_utc: str | None
    raw_session_started_at: str | None
    session_cwd: str | None
    originator: str | None
    cli_version: str | None


@dataclass(frozen=True)
class AgentRunRecord:
    agent_run_key: str
    session_key: str
    lineage_key: str
    parent_agent_run_key: str | None
    raw_parent_agent_run_id: str | None
    agent_name: str | None
    agent_role: str | None
    model_id: str | None
    started_at_utc: str | None
    ended_at_utc: str | None
    raw_metadata_json: str


@dataclass(frozen=True)
class UsageEventRecord:
    event_id: str
    event_index: int
    source_line: int
    event_type: str
    payload_type: str | None
    event_ts_utc: str | None
    raw_event_timestamp: str | None
    turn_id: str | None
    turn_index: int | None
    raw_cwd: str | None
    session_cwd: str | None
    workspace: WorkspaceRecord
    model_id: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_output_tokens: int | None
    total_tokens: int | None
    agent_run_key: str | None
    raw_event_json: str


@dataclass(frozen=True)
class ParsedFile:
    provider: str
    host: str
    source_kind: SourceKind
    file_extension: str
    line_count: int
    parse_status: str
    parse_error: str | None
    session: ProviderSessionRecord | None
    agent_runs: tuple[AgentRunRecord, ...]
    events: tuple[UsageEventRecord, ...]
    workspaces: tuple[WorkspaceRecord, ...]
    model_ids: tuple[str, ...]


@dataclass(frozen=True)
class ImportBatchSummary:
    batch_id: str
    manifest_relpath: str
    scanned_file_count: int
    imported_file_count: int
    skipped_file_count: int
    failed_file_count: int


@dataclass(frozen=True)
class ImportOutcome:
    source_path: Path
    source_kind: SourceKind
    status: str
    detail: str | None
    raw_file_id: str | None
    content_hash: str | None
    stored_relpath: str | None
    event_count: int
