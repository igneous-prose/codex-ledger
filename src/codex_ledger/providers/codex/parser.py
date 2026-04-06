from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codex_ledger.domain.records import (
    AgentRunRecord,
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


def parse_local_rollout_file(path: Path) -> ParsedFile:
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
    current_model_id: str | None = None
    host = host_override or "standalone_cli"
    provider = provider_override or "codex"
    events: list[UsageEventRecord] = []
    workspaces: dict[str, WorkspaceRecord] = {}
    models: set[str] = set()

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
            current_turn_cwd = _clean_str(payload.get("cwd"))
            current_model_id = _clean_str(payload.get("model"))

        session_cwd = _clean_str(session_payload.get("cwd"))
        raw_cwd = _extract_event_cwd(record_type, payload, current_turn_cwd)
        workspace = resolve_workspace(raw_cwd, session_cwd)
        workspaces[workspace.workspace_key] = workspace

        model_id = (
            _clean_str(payload.get("model_id"))
            or _clean_str(payload.get("model"))
            or current_model_id
        )
        if model_id:
            models.add(model_id)

        usage = _extract_usage(payload)
        raw_timestamp = _clean_str(record.get("timestamp"))
        event = UsageEventRecord(
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
            raw_event_json=canonical_json(record),
        )
        events.append(event)

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
    root_agent = AgentRunRecord(
        agent_run_key=sha256_text(f"{session.session_key}:root")[:32],
        session_key=session.session_key,
        lineage_key="root",
        parent_agent_run_key=None,
        raw_parent_agent_run_id=None,
        agent_name="primary",
        agent_role="root",
        model_id=_first(sorted(models)),
        started_at_utc=session.session_started_at_utc,
        ended_at_utc=session.session_ended_at_utc,
        raw_metadata_json=canonical_json(
            {
                "source_kind": source_kind,
                "lineage": "placeholder_root",
            }
        ),
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
        agent_runs=(root_agent,),
        events=tuple(events),
        workspaces=tuple(sorted(workspaces.values(), key=lambda item: item.workspace_key)),
        model_ids=tuple(sorted(models)),
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
