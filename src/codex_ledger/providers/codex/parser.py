from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codex_ledger.domain.records import (
    AgentKind,
    AgentRunRecord,
    LineageConfidence,
    LineageStatus,
    ParsedFile,
    ProviderSessionRecord,
    SourceKind,
    UsageEventRecord,
    WorkspaceRecord,
)
from codex_ledger.normalize.workspaces import resolve_workspace
from codex_ledger.utils.hashing import sha256_text
from codex_ledger.utils.json import canonical_json
from codex_ledger.utils.time import normalize_timestamp

MAX_IMPORT_FILE_BYTES = 64 * 1024 * 1024


def parse_local_rollout_file(path: Path) -> ParsedFile:
    size_bytes = path.stat().st_size
    if size_bytes > MAX_IMPORT_FILE_BYTES:
        return _file_too_large(
            path=path,
            source_kind="local_rollout_file",
            size_bytes=size_bytes,
            max_bytes=MAX_IMPORT_FILE_BYTES,
        )
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        return ParsedFile(
            provider="codex",
            host="unknown",
            source_kind="local_rollout_file",
            file_extension=path.suffix,
            line_count=0,
            parse_status="malformed_unicode",
            parse_error=str(exc),
            session=None,
            agent_runs=(),
            events=(),
            workspaces=(),
            model_ids=(),
        )

    raw_records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if line.strip() == "":
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            return ParsedFile(
                provider="codex",
                host="unknown",
                source_kind="local_rollout_file",
                file_extension=path.suffix,
                line_count=len(lines),
                parse_status="malformed_jsonl",
                parse_error=f"line {line_number}: {exc.msg}",
                session=None,
                agent_runs=(),
                events=(),
                workspaces=(),
                model_ids=(),
            )
        raw_records.append(payload)

    return _build_parsed_rollout(
        records=raw_records,
        file_extension=path.suffix,
        source_kind="local_rollout_file",
    )


def parse_imported_json_report(path: Path) -> ParsedFile:
    size_bytes = path.stat().st_size
    if size_bytes > MAX_IMPORT_FILE_BYTES:
        return _file_too_large(
            path=path,
            source_kind="imported_json_report",
            size_bytes=size_bytes,
            max_bytes=MAX_IMPORT_FILE_BYTES,
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return ParsedFile(
            provider="codex",
            host="unknown",
            source_kind="imported_json_report",
            file_extension=path.suffix,
            line_count=0,
            parse_status="malformed_unicode",
            parse_error=str(exc),
            session=None,
            agent_runs=(),
            events=(),
            workspaces=(),
            model_ids=(),
        )
    except json.JSONDecodeError as exc:
        return ParsedFile(
            provider="codex",
            host="unknown",
            source_kind="imported_json_report",
            file_extension=path.suffix,
            line_count=1,
            parse_status="malformed_json",
            parse_error=exc.msg,
            session=None,
            agent_runs=(),
            events=(),
            workspaces=(),
            model_ids=(),
        )

    if isinstance(document, list):
        report_obj: dict[str, Any] = {"events": document}
    elif isinstance(document, dict):
        report_obj = document
    else:
        return ParsedFile(
            provider="codex",
            host="unknown",
            source_kind="imported_json_report",
            file_extension=path.suffix,
            line_count=1,
            parse_status="unsupported_json_shape",
            parse_error="expected object or array",
            session=None,
            agent_runs=(),
            events=(),
            workspaces=(),
            model_ids=(),
        )

    events_obj = report_obj.get("events", [])
    if not isinstance(events_obj, list):
        return ParsedFile(
            provider="codex",
            host="unknown",
            source_kind="imported_json_report",
            file_extension=path.suffix,
            line_count=1,
            parse_status="unsupported_json_shape",
            parse_error="events must be a list",
            session=None,
            agent_runs=(),
            events=(),
            workspaces=(),
            model_ids=(),
        )

    session_meta = report_obj.get("session", {})
    if not isinstance(session_meta, dict):
        session_meta = {}

    records: list[dict[str, Any]] = [
        {
            "timestamp": session_meta.get("timestamp"),
            "type": "session_meta",
            "payload": session_meta,
        }
    ]
    for event in events_obj:
        if isinstance(event, dict):
            records.append(
                {
                    "timestamp": event.get("timestamp") or event.get("event_timestamp"),
                    "type": event.get("type") or event.get("event_type") or "report_event",
                    "payload": event,
                }
            )

    return _build_parsed_rollout(
        records=records,
        file_extension=path.suffix,
        source_kind="imported_json_report",
        provider_override=_clean_str(report_obj.get("provider")) or "codex",
        host_override=_clean_str(report_obj.get("host")) or "imported_json",
    )


def _build_parsed_rollout(
    *,
    records: list[dict[str, Any]],
    file_extension: str,
    source_kind: SourceKind,
    provider_override: str | None = None,
    host_override: str | None = None,
) -> ParsedFile:
    session_payload: dict[str, Any] = {}
    current_turn_id: str | None = None
    current_turn_index: int | None = None
    current_turn_cwd: str | None = None
    current_requested_model: str | None = None
    host = host_override or "standalone_cli"
    provider = provider_override or "codex"
    events: list[UsageEventRecord] = []
    workspaces: dict[str, WorkspaceRecord] = {}
    observed_models: set[str] = set()
    requested_models: set[str] = set()
    spawned_children: list[dict[str, str]] = []

    for event_index, record in enumerate(records, start=1):
        record_type = _clean_str(record.get("type")) or "unknown"
        payload_raw = record.get("payload")
        payload = payload_raw if isinstance(payload_raw, dict) else {}
        payload_type = _clean_str(payload.get("type"))

        if record_type == "session_meta":
            session_payload = payload
            host = (
                _clean_str(payload.get("source")) or _clean_str(payload.get("originator")) or host
            )
        elif record_type == "turn_context":
            current_turn_index = (current_turn_index or 0) + 1
            current_turn_id = _clean_str(payload.get("turn_id"))
            current_turn_cwd = _clean_str(payload.get("cwd")) or _nested_turn_context_cwd(payload)
            current_requested_model = _clean_str(payload.get("model")) or _clean_str(
                payload.get("model_id")
            )
            if current_requested_model is not None:
                requested_models.add(current_requested_model)

        spawned_child = _extract_spawned_child(record, payload)
        if spawned_child is not None:
            spawned_children.append(spawned_child)
            requested_model = spawned_child.get("requested_model_id")
            if requested_model is not None:
                requested_models.add(requested_model)

        session_cwd = _clean_str(session_payload.get("cwd"))
        raw_cwd = _extract_event_cwd(record_type, payload, current_turn_cwd)
        workspace = resolve_workspace(raw_cwd, session_cwd)
        workspaces[workspace.workspace_key] = workspace

        model_id = (
            _clean_str(payload.get("model_id"))
            or _clean_str(payload.get("model"))
            or current_requested_model
        )
        if model_id:
            observed_models.add(model_id)

        usage = _extract_usage(payload)
        raw_timestamp = _clean_str(record.get("timestamp"))
        events.append(
            UsageEventRecord(
                event_id=sha256_text(f"{source_kind}:{event_index}:{canonical_json(record)}"),
                event_index=event_index,
                source_line=event_index,
                event_type=record_type,
                payload_type=payload_type,
                event_ts_utc=normalize_timestamp(raw_timestamp),
                raw_event_timestamp=raw_timestamp,
                turn_id=_clean_str(payload.get("turn_id")) or current_turn_id,
                turn_index=_coerce_int(payload.get("turn_index")) or current_turn_index,
                raw_cwd=raw_cwd,
                session_cwd=session_cwd,
                workspace=workspace,
                model_id=model_id,
                input_tokens=usage.get("input_tokens"),
                cached_input_tokens=usage.get("cached_input_tokens"),
                output_tokens=usage.get("output_tokens"),
                reasoning_output_tokens=usage.get("reasoning_output_tokens"),
                total_tokens=usage.get("total_tokens"),
                agent_run_key=None,
                raw_event_json=canonical_json(record),
            )
        )

    session_id = (
        _clean_str(session_payload.get("id"))
        or sha256_text(canonical_json(records[:1]) if records else "empty")[:16]
    )
    session_started_raw = _clean_str(session_payload.get("timestamp"))
    event_timestamps = [event.event_ts_utc for event in events if event.event_ts_utc is not None]
    session = ProviderSessionRecord(
        session_key=sha256_text(f"{provider}:{host}:{session_id}")[:32],
        raw_session_id=session_id,
        session_meta_json=canonical_json(session_payload),
        session_started_at_utc=normalize_timestamp(session_started_raw) or _first(event_timestamps),
        session_ended_at_utc=_last(event_timestamps),
        raw_session_started_at=session_started_raw,
        session_cwd=_clean_str(session_payload.get("cwd")),
        originator=_clean_str(session_payload.get("originator")),
        cli_version=_clean_str(session_payload.get("cli_version")),
    )
    primary_agent = _build_primary_agent_run(
        session=session,
        session_payload=session_payload,
        source_kind=source_kind,
        requested_models=tuple(sorted(requested_models)),
        observed_models=tuple(sorted(observed_models)),
        spawned_children=tuple(spawned_children),
    )
    spawn_agent_runs = tuple(
        _build_spawn_agent_run(
            session=session,
            parent_agent_run_key=primary_agent.agent_run_key,
            spawned_child=spawned_child,
        )
        for spawned_child in spawned_children
    )
    attributed_events = tuple(
        _assign_event_agent_run_key(event, primary_agent.agent_run_key) for event in events
    )
    return ParsedFile(
        provider=provider,
        host=host,
        source_kind=source_kind,
        file_extension=file_extension,
        line_count=len(records),
        parse_status="parsed",
        parse_error=None,
        session=session,
        agent_runs=(primary_agent, *spawn_agent_runs),
        events=attributed_events,
        workspaces=tuple(sorted(workspaces.values(), key=lambda item: item.workspace_key)),
        model_ids=tuple(sorted(observed_models | requested_models)),
    )


def _build_primary_agent_run(
    *,
    session: ProviderSessionRecord,
    session_payload: dict[str, Any],
    source_kind: SourceKind,
    requested_models: tuple[str, ...],
    observed_models: tuple[str, ...],
    spawned_children: tuple[dict[str, str], ...],
) -> AgentRunRecord:
    parent_raw_session_id = _extract_parent_session_id(session_payload)
    source_thread_spawn = _extract_thread_spawn(session_payload)
    agent_name = _clean_str(session_payload.get("agent_nickname")) or _clean_str(
        source_thread_spawn.get("agent_nickname")
    )
    agent_role = _clean_str(session_payload.get("agent_role")) or _clean_str(
        source_thread_spawn.get("agent_role")
    )

    if parent_raw_session_id is not None or agent_name is not None or agent_role is not None:
        lineage_key = "session"
        agent_kind: AgentKind = "subagent"
        lineage_status: LineageStatus = "child_only_orphaned"
        lineage_confidence: LineageConfidence = "session_metadata_only"
        unresolved_reason = "parent_session_missing"
    else:
        lineage_key = "root"
        agent_kind = "root"
        lineage_status = "root_placeholder"
        lineage_confidence = "placeholder"
        unresolved_reason = None
        agent_name = "primary"
        agent_role = "root"

    metadata: dict[str, Any] = {
        "source_kind": source_kind,
        "spawned_child_count": len(spawned_children),
    }
    if parent_raw_session_id is not None:
        metadata["parent_raw_session_id"] = parent_raw_session_id
    if source_thread_spawn:
        metadata["thread_spawn"] = source_thread_spawn
    if spawned_children:
        metadata["spawned_children"] = list(spawned_children)

    return AgentRunRecord(
        agent_run_key=sha256_text(f"{session.session_key}:{lineage_key}")[:32],
        session_key=session.session_key,
        lineage_key=lineage_key,
        parent_agent_run_key=None,
        raw_parent_agent_run_id=parent_raw_session_id,
        agent_kind=agent_kind,
        agent_name=agent_name,
        agent_role=agent_role,
        requested_model_id=_first(requested_models) or _first(observed_models),
        model_id=_first(observed_models),
        lineage_status=lineage_status,
        lineage_confidence=lineage_confidence,
        unresolved_reason=unresolved_reason,
        started_at_utc=session.session_started_at_utc,
        ended_at_utc=session.session_ended_at_utc,
        raw_metadata_json=canonical_json(metadata),
    )


def _build_spawn_agent_run(
    *,
    session: ProviderSessionRecord,
    parent_agent_run_key: str,
    spawned_child: dict[str, str],
) -> AgentRunRecord:
    child_thread_id = spawned_child["child_thread_id"]
    metadata = canonical_json(spawned_child)
    return AgentRunRecord(
        agent_run_key=sha256_text(f"{session.session_key}:spawn:{child_thread_id}")[:32],
        session_key=session.session_key,
        lineage_key=f"spawn:{child_thread_id}",
        parent_agent_run_key=parent_agent_run_key,
        raw_parent_agent_run_id=session.raw_session_id,
        agent_kind="subagent",
        agent_name=spawned_child.get("agent_name"),
        agent_role=spawned_child.get("agent_role"),
        requested_model_id=spawned_child.get("requested_model_id"),
        model_id=None,
        lineage_status="spawn_only_unmatched",
        lineage_confidence="spawn_event_only",
        unresolved_reason="child_session_missing",
        started_at_utc=spawned_child.get("started_at_utc"),
        ended_at_utc=spawned_child.get("started_at_utc"),
        raw_metadata_json=metadata,
    )


def _assign_event_agent_run_key(
    event: UsageEventRecord,
    agent_run_key: str,
) -> UsageEventRecord:
    return UsageEventRecord(
        event_id=event.event_id,
        event_index=event.event_index,
        source_line=event.source_line,
        event_type=event.event_type,
        payload_type=event.payload_type,
        event_ts_utc=event.event_ts_utc,
        raw_event_timestamp=event.raw_event_timestamp,
        turn_id=event.turn_id,
        turn_index=event.turn_index,
        raw_cwd=event.raw_cwd,
        session_cwd=event.session_cwd,
        workspace=event.workspace,
        model_id=event.model_id,
        input_tokens=event.input_tokens,
        cached_input_tokens=event.cached_input_tokens,
        output_tokens=event.output_tokens,
        reasoning_output_tokens=event.reasoning_output_tokens,
        total_tokens=event.total_tokens,
        agent_run_key=agent_run_key,
        raw_event_json=event.raw_event_json,
    )


def _extract_event_cwd(
    record_type: str,
    payload: dict[str, Any],
    current_turn_cwd: str | None,
) -> str | None:
    if record_type == "turn_context":
        return _clean_str(payload.get("cwd")) or _nested_turn_context_cwd(payload)

    nested_cwd = _nested_turn_context_cwd(payload)
    if nested_cwd:
        return nested_cwd

    return _clean_str(payload.get("cwd")) or current_turn_cwd


def _nested_turn_context_cwd(payload: dict[str, Any]) -> str | None:
    turn_context = payload.get("turn_context")
    if isinstance(turn_context, dict):
        return _clean_str(turn_context.get("cwd"))
    return None


def _extract_spawned_child(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, str] | None:
    if _clean_str(payload.get("type")) != "collab_agent_spawn_end":
        return None

    child_thread_id = _clean_str(payload.get("new_thread_id"))
    if child_thread_id is None:
        return None

    child: dict[str, str] = {"child_thread_id": child_thread_id}
    for key, out_key in (
        ("new_agent_nickname", "agent_name"),
        ("new_agent_role", "agent_role"),
        ("model", "requested_model_id"),
        ("sender_thread_id", "parent_thread_id"),
        ("reasoning_effort", "reasoning_effort"),
        ("status", "spawn_status"),
    ):
        value = _clean_str(payload.get(key))
        if value is not None:
            child[out_key] = value
    timestamp = normalize_timestamp(_clean_str(record.get("timestamp")))
    if timestamp is not None:
        child["started_at_utc"] = timestamp
    return child


def _extract_parent_session_id(session_payload: dict[str, Any]) -> str | None:
    return _clean_str(session_payload.get("forked_from_id")) or _clean_str(
        _extract_thread_spawn(session_payload).get("parent_thread_id")
    )


def _extract_thread_spawn(session_payload: dict[str, Any]) -> dict[str, str]:
    source = session_payload.get("source")
    if not isinstance(source, dict):
        return {}
    subagent = source.get("subagent")
    if not isinstance(subagent, dict):
        return {}
    thread_spawn = subagent.get("thread_spawn")
    if not isinstance(thread_spawn, dict):
        return {}

    cleaned: dict[str, str] = {}
    for key in ("parent_thread_id", "agent_nickname", "agent_role"):
        value = _clean_str(thread_spawn.get(key))
        if value is not None:
            cleaned[key] = value
    depth = thread_spawn.get("depth")
    if isinstance(depth, int):
        cleaned["depth"] = str(depth)
    return cleaned


def _extract_usage(payload: dict[str, Any]) -> dict[str, int | None]:
    if payload.get("type") == "token_count":
        info = payload.get("info")
        if isinstance(info, dict):
            last_usage = info.get("last_token_usage")
            if isinstance(last_usage, dict):
                return {
                    "input_tokens": _coerce_int(last_usage.get("input_tokens")),
                    "cached_input_tokens": _coerce_int(last_usage.get("cached_input_tokens")),
                    "output_tokens": _coerce_int(last_usage.get("output_tokens")),
                    "reasoning_output_tokens": _coerce_int(
                        last_usage.get("reasoning_output_tokens")
                    ),
                    "total_tokens": _coerce_int(last_usage.get("total_tokens")),
                }

    usage = payload.get("usage")
    if isinstance(usage, dict):
        return {
            "input_tokens": _coerce_int(usage.get("input_tokens")),
            "cached_input_tokens": _coerce_int(usage.get("cached_input_tokens")),
            "output_tokens": _coerce_int(usage.get("output_tokens")),
            "reasoning_output_tokens": _coerce_int(
                usage.get("reasoning_output_tokens") or usage.get("reasoning_tokens")
            ),
            "total_tokens": _coerce_int(usage.get("total_tokens")),
        }

    return {
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_output_tokens": None,
        "total_tokens": None,
    }


def _file_too_large(
    *,
    path: Path,
    source_kind: SourceKind,
    size_bytes: int,
    max_bytes: int,
) -> ParsedFile:
    return ParsedFile(
        provider="codex",
        host="unknown",
        source_kind=source_kind,
        file_extension=path.suffix,
        line_count=0,
        parse_status="file_too_large",
        parse_error=(
            f"source file exceeds configured limit ({size_bytes} bytes > {max_bytes} bytes)"
        ),
        session=None,
        agent_runs=(),
        events=(),
        workspaces=(),
        model_ids=(),
    )


def _clean_str(value: Any) -> str | None:
    if isinstance(value, str) and value != "":
        return value
    return None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _first(values: list[str] | tuple[str, ...]) -> str | None:
    return values[0] if values else None


def _last(values: list[str] | tuple[str, ...]) -> str | None:
    return values[-1] if values else None
