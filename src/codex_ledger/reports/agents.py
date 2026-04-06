from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from codex_ledger.domain.records import RedactionMode
from codex_ledger.normalize.privacy import DEFAULT_REDACTION_MODE, render_workspace_label
from codex_ledger.storage.migrations import connect_database, default_database_path
from codex_ledger.storage.repository import fetch_workspace_alias_map

DIAGNOSTIC_SCHEMA_VERSION = "phase2.1-agent-diagnostics-v1"


def build_agent_report(
    *,
    archive_home: Path,
    period: str,
    as_of: date,
    redaction_mode: RedactionMode = DEFAULT_REDACTION_MODE,
) -> dict[str, Any]:
    database_path = default_database_path(archive_home)
    with connect_database(database_path) as connection:
        return _build_agent_report_payload(
            connection=connection,
            period=period,
            as_of=as_of,
            redaction_mode=redaction_mode,
        )


def explain_agent_run(
    *,
    archive_home: Path,
    agent_run_key: str,
    redaction_mode: RedactionMode = DEFAULT_REDACTION_MODE,
) -> dict[str, Any]:
    database_path = default_database_path(archive_home)
    with connect_database(database_path) as connection:
        return _build_agent_explain_payload(
            connection=connection,
            agent_run_key=agent_run_key,
            redaction_mode=redaction_mode,
        )


def format_agent_report_table(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        f"Agent diagnostics: {payload['period']['period']} as of {payload['period']['as_of']}",
        (
            "Root usage: "
            f"{summary['root_usage']['event_count']} events, "
            f"{summary['root_usage']['total_tokens']} tokens"
        ),
        (
            "Subagent usage: "
            f"{summary['subagent_usage']['event_count']} events, "
            f"{summary['subagent_usage']['total_tokens']} tokens"
        ),
        (
            "Lineage counts: "
            f"matched={summary['matched_child_count']}, "
            f"unresolved_spawns={summary['unresolved_spawn_count']}, "
            f"orphan_children={summary['orphan_child_count']}"
        ),
        "Top heavy hitters:",
    ]
    for item in payload["top_heavy_hitters"][:5]:
        label = item["agent_name"] or item["agent_role"] or item["agent_run_key"]
        lines.append(
            f"- {label}: {item['total_tokens']} tokens across {item['event_count']} events"
        )
    return "\n".join(lines)


def format_agent_explain_table(payload: dict[str, Any]) -> str:
    run = payload["agent_run"]
    summary = payload["event_summary"]
    lines = [
        f"Agent run: {run['agent_run_key']}",
        f"Kind: {run['agent_kind']}",
        f"Name: {run['agent_name'] or 'unknown'}",
        f"Role: {run['agent_role'] or 'unknown'}",
        (
            "Lineage: "
            f"{run['lineage_status']} "
            f"({run['lineage_confidence']})"
        ),
        f"Requested model: {run['requested_model_id'] or 'unknown'}",
        f"Observed model: {run['observed_model_id'] or 'unknown'}",
        f"Events: {summary['event_count']}",
        f"Tokens: {summary['total_tokens']}",
        f"Stored raw artifact: {payload['provenance']['stored_relpath']}",
    ]
    return "\n".join(lines)


def _build_agent_report_payload(
    *,
    connection: sqlite3.Connection,
    period: str,
    as_of: date,
    redaction_mode: RedactionMode,
) -> dict[str, Any]:
    start_utc, end_utc = _period_bounds(period, as_of)
    alias_map = fetch_workspace_alias_map(connection)
    agent_runs = _fetch_agent_runs(connection, start_utc, end_utc)
    event_rows = _fetch_agent_event_rows(
        connection,
        start_utc,
        end_utc,
        redaction_mode=redaction_mode,
        alias_map=alias_map,
    )

    root_usage = _usage_bucket()
    subagent_usage = _usage_bucket()
    usage_by_name: dict[str, dict[str, Any]] = {}
    usage_by_role: dict[str, dict[str, Any]] = {}
    usage_by_requested_model: dict[str, dict[str, Any]] = {}
    usage_by_observed_model: dict[str, dict[str, Any]] = {}
    heavy_hitters: dict[str, dict[str, Any]] = {}
    workspace_spread: dict[str, set[str]] = defaultdict(set)
    workspace_labels_by_key: dict[str, str] = {}

    for row in event_rows:
        bucket = root_usage if row["agent_kind"] == "root" else subagent_usage
        _add_usage(bucket, row)

        agent_name = row["agent_name"] or "unknown"
        agent_role = row["agent_role"] or "unknown"
        requested_model = row["requested_model_id"] or "unknown"
        observed_model = row["observed_model_id"] or "unknown"

        _add_group_usage(usage_by_name, agent_name, row)
        _add_group_usage(usage_by_role, agent_role, row)
        _add_group_usage(usage_by_requested_model, requested_model, row)
        _add_group_usage(usage_by_observed_model, observed_model, row)

        hitter = heavy_hitters.setdefault(
            row["agent_run_key"],
            {
                "agent_run_key": row["agent_run_key"],
                "agent_name": row["agent_name"],
                "agent_role": row["agent_role"],
                "agent_kind": row["agent_kind"],
                "lineage_status": row["lineage_status"],
                "requested_model_id": row["requested_model_id"],
                "observed_model_id": row["observed_model_id"],
                "event_count": 0,
                "total_tokens": 0,
                "workspace_labels": set(),
            },
        )
        hitter["event_count"] += 1
        hitter["total_tokens"] += row["total_tokens"]
        hitter["workspace_labels"].add(row["workspace_label"])

        workspace_spread[agent_name].add(row["workspace_key"])
        workspace_labels_by_key[row["workspace_key"]] = row["workspace_label"]

    status_mix = _count_mix(agent_runs, "lineage_status")
    confidence_mix = _count_mix(agent_runs, "lineage_confidence")

    workspace_spread_items = []
    for agent_name, workspace_keys in sorted(workspace_spread.items()):
        labels = sorted(workspace_labels_by_key[key] for key in workspace_keys)
        workspace_spread_items.append(
            {
                "agent_name": agent_name,
                "workspace_count": len(workspace_keys),
                "workspace_labels": labels,
            }
        )

    heavy_hitter_items = []
    for item in heavy_hitters.values():
        heavy_hitter_items.append(
            {
                **item,
                "workspace_count": len(item["workspace_labels"]),
                "workspace_labels": sorted(item["workspace_labels"]),
            }
        )

    payload = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "period": {
            "period": period,
            "as_of": as_of.isoformat(),
            "start_utc": start_utc,
            "end_exclusive_utc": end_utc,
        },
        "summary": {
            "root_usage": root_usage,
            "subagent_usage": subagent_usage,
            "matched_child_count": sum(
                1
                for row in agent_runs
                if row["agent_kind"] == "subagent"
                and row["lineage_key"] == "session"
                and row["lineage_status"] == "resolved"
            ),
            "unresolved_spawn_count": sum(
                1 for row in agent_runs if row["lineage_status"] == "spawn_only_unmatched"
            ),
            "orphan_child_count": sum(
                1 for row in agent_runs if row["lineage_status"] == "child_only_orphaned"
            ),
            "lineage_status_mix": status_mix,
            "lineage_confidence_mix": confidence_mix,
        },
        "usage_by_agent_name": _sorted_usage_groups(usage_by_name),
        "usage_by_agent_role": _sorted_usage_groups(usage_by_role),
        "usage_by_requested_model": _sorted_usage_groups(usage_by_requested_model),
        "usage_by_observed_model": _sorted_usage_groups(usage_by_observed_model),
        "top_heavy_hitters": sorted(
            heavy_hitter_items,
            key=lambda item: (-int(item["total_tokens"]), str(item["agent_run_key"])),
        ),
        "workspace_spread_by_agent": workspace_spread_items,
    }
    return payload


def _build_agent_explain_payload(
    *,
    connection: sqlite3.Connection,
    agent_run_key: str,
    redaction_mode: RedactionMode,
) -> dict[str, Any]:
    alias_map = fetch_workspace_alias_map(connection)
    row = connection.execute(
        """
        SELECT ar.agent_run_key,
               ar.session_key,
               ar.lineage_key,
               ar.parent_agent_run_key,
               ar.raw_parent_agent_run_id,
               ar.agent_kind,
               ar.agent_name,
               ar.agent_role,
               ar.requested_model_id,
               ar.model_id,
               ar.lineage_status,
               ar.lineage_confidence,
               ar.unresolved_reason,
               ar.started_at_utc,
               ar.ended_at_utc,
               ar.raw_metadata_json,
               ar.source_kind,
               ps.raw_session_id,
               rf.raw_file_id,
               rf.stored_relpath
        FROM agent_runs AS ar
        JOIN provider_sessions AS ps
          ON ps.session_key = ar.session_key
        JOIN raw_files AS rf
          ON rf.raw_file_id = ar.raw_file_id
        WHERE ar.agent_run_key = ?
        """,
        (agent_run_key,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown agent run: {agent_run_key}")

    event_rows = connection.execute(
        """
        SELECT ue.event_id,
               ue.event_index,
               ue.event_type,
               ue.payload_type,
               ue.event_ts_utc,
               ue.model_id,
               ue.total_tokens,
               ue.input_tokens,
               ue.cached_input_tokens,
               ue.output_tokens,
               ue.reasoning_output_tokens,
               ue.workspace_key,
               w.display_label,
               w.redacted_display_label,
               w.resolved_root_path,
               w.resolution_strategy
        FROM usage_events AS ue
        JOIN workspaces AS w
          ON w.workspace_key = ue.workspace_key
        WHERE ue.agent_run_key = ?
        ORDER BY ue.event_index
        """,
        (agent_run_key,),
    ).fetchall()

    workspace_items: dict[str, dict[str, str]] = {}
    observed_model_ids: set[str] = set()
    total_tokens = 0
    for event in event_rows:
        workspace = {
            "workspace_key": str(event[11]),
            "display_label": str(event[12]),
            "redacted_display_label": str(event[13]),
            "resolved_root_path": str(event[14]),
            "resolution_strategy": str(event[15]),
        }
        workspace_label = render_workspace_label(
            _workspace_proxy(workspace),
            mode=redaction_mode,
            aliases=alias_map,
        )
        workspace_items[workspace["workspace_key"]] = {
            "workspace_key": workspace["workspace_key"],
            "workspace_label": workspace_label,
            "resolution_strategy": workspace["resolution_strategy"],
        }
        model_id = event[5]
        if model_id is not None:
            observed_model_ids.add(str(model_id))
        total_tokens += int(event[6] or 0)

    payload = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "agent_run": {
            "agent_run_key": str(row[0]),
            "session_key": str(row[1]),
            "lineage_key": str(row[2]),
            "parent_agent_run_key": row[3] if row[3] is None else str(row[3]),
            "raw_parent_agent_run_id": row[4] if row[4] is None else str(row[4]),
            "agent_kind": str(row[5]),
            "agent_name": row[6] if row[6] is None else str(row[6]),
            "agent_role": row[7] if row[7] is None else str(row[7]),
            "requested_model_id": row[8] if row[8] is None else str(row[8]),
            "observed_model_id": row[9] if row[9] is None else str(row[9]),
            "lineage_status": str(row[10]),
            "lineage_confidence": str(row[11]),
            "unresolved_reason": row[12] if row[12] is None else str(row[12]),
            "started_at_utc": row[13] if row[13] is None else str(row[13]),
            "ended_at_utc": row[14] if row[14] is None else str(row[14]),
            "raw_metadata_json": str(row[15]),
        },
        "session": {
            "raw_session_id": str(row[17]),
        },
        "provenance": {
            "source_kind": str(row[16]),
            "raw_file_id": str(row[18]),
            "stored_relpath": str(row[19]),
        },
        "event_summary": {
            "event_count": len(event_rows),
            "total_tokens": total_tokens,
            "observed_model_ids": sorted(observed_model_ids),
        },
        "workspace_attribution": [
            workspace_items[key] for key in sorted(workspace_items)
        ],
    }
    return payload


def _fetch_agent_runs(
    connection: sqlite3.Connection,
    start_utc: str,
    end_utc: str,
) -> list[dict[str, str | None]]:
    rows = connection.execute(
        """
        SELECT agent_run_key,
               lineage_key,
               agent_kind,
               agent_name,
               agent_role,
               requested_model_id,
               model_id,
               lineage_status,
               lineage_confidence,
               unresolved_reason,
               COALESCE(started_at_utc, ended_at_utc)
        FROM agent_runs
        WHERE COALESCE(started_at_utc, ended_at_utc) >= ?
          AND COALESCE(started_at_utc, ended_at_utc) < ?
        ORDER BY agent_run_key
        """,
        (start_utc, end_utc),
    ).fetchall()
    return [
        {
            "agent_run_key": str(row[0]),
            "lineage_key": str(row[1]),
            "agent_kind": str(row[2]),
            "agent_name": None if row[3] is None else str(row[3]),
            "agent_role": None if row[4] is None else str(row[4]),
            "requested_model_id": None if row[5] is None else str(row[5]),
            "model_id": None if row[6] is None else str(row[6]),
            "lineage_status": str(row[7]),
            "lineage_confidence": str(row[8]),
            "unresolved_reason": None if row[9] is None else str(row[9]),
            "started_or_ended_at_utc": None if row[10] is None else str(row[10]),
        }
        for row in rows
    ]


def _fetch_agent_event_rows(
    connection: sqlite3.Connection,
    start_utc: str,
    end_utc: str,
    *,
    redaction_mode: RedactionMode,
    alias_map: dict[str, str],
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT ue.agent_run_key,
               ue.workspace_key,
               w.display_label,
               w.redacted_display_label,
               w.resolved_root_path,
               ar.agent_kind,
               ar.agent_name,
               ar.agent_role,
               ar.requested_model_id,
               ue.model_id,
               ar.lineage_status,
               ue.total_tokens,
               ue.input_tokens,
               ue.cached_input_tokens,
               ue.output_tokens,
               ue.reasoning_output_tokens,
               w.resolution_strategy
        FROM usage_events AS ue
        JOIN agent_runs AS ar
          ON ar.agent_run_key = ue.agent_run_key
        JOIN workspaces AS w
          ON w.workspace_key = ue.workspace_key
        WHERE ue.event_ts_utc >= ?
          AND ue.event_ts_utc < ?
        ORDER BY ue.event_id
        """,
        (start_utc, end_utc),
    ).fetchall()
    items = []
    for row in rows:
        workspace = _workspace_proxy(
            {
                "workspace_key": str(row[1]),
                "display_label": str(row[2]),
                "redacted_display_label": str(row[3]),
                "resolved_root_path": str(row[4]),
                "resolution_strategy": str(row[16]),
            }
        )
        items.append(
            {
                "agent_run_key": str(row[0]),
                "workspace_key": str(row[1]),
                "workspace_label": render_workspace_label(
                    workspace,
                    mode=redaction_mode,
                    aliases=alias_map,
                ),
                "agent_kind": str(row[5]),
                "agent_name": None if row[6] is None else str(row[6]),
                "agent_role": None if row[7] is None else str(row[7]),
                "requested_model_id": None if row[8] is None else str(row[8]),
                "observed_model_id": None if row[9] is None else str(row[9]),
                "lineage_status": str(row[10]),
                "total_tokens": int(row[11] or 0),
                "input_tokens": int(row[12] or 0),
                "cached_input_tokens": int(row[13] or 0),
                "output_tokens": int(row[14] or 0),
                "reasoning_output_tokens": int(row[15] or 0),
            }
        )
    return items


def _period_bounds(period: str, as_of: date) -> tuple[str, str]:
    if period == "day":
        start = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)
        end = start + timedelta(days=1)
    elif period == "week":
        start_date = as_of - timedelta(days=as_of.weekday())
        start = datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)
        end = start + timedelta(days=7)
    elif period == "month":
        start = datetime(as_of.year, as_of.month, 1, tzinfo=UTC)
        if as_of.month == 12:
            end = datetime(as_of.year + 1, 1, 1, tzinfo=UTC)
        else:
            end = datetime(as_of.year, as_of.month + 1, 1, tzinfo=UTC)
    elif period == "year":
        start = datetime(as_of.year, 1, 1, tzinfo=UTC)
        end = datetime(as_of.year + 1, 1, 1, tzinfo=UTC)
    else:
        raise ValueError(f"Unsupported period: {period}")

    return (
        start.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )


def _usage_bucket() -> dict[str, int]:
    return {
        "event_count": 0,
        "total_tokens": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }


def _add_usage(bucket: dict[str, int], row: dict[str, Any]) -> None:
    bucket["event_count"] += 1
    bucket["total_tokens"] += int(row["total_tokens"])
    bucket["input_tokens"] += int(row["input_tokens"])
    bucket["cached_input_tokens"] += int(row["cached_input_tokens"])
    bucket["output_tokens"] += int(row["output_tokens"])
    bucket["reasoning_output_tokens"] += int(row["reasoning_output_tokens"])


def _add_group_usage(groups: dict[str, dict[str, Any]], key: str, row: dict[str, Any]) -> None:
    bucket = groups.setdefault(
        key,
        {
            "label": key,
            "event_count": 0,
            "total_tokens": 0,
            "agent_run_count": set(),
        },
    )
    bucket["event_count"] += 1
    bucket["total_tokens"] += int(row["total_tokens"])
    bucket["agent_run_count"].add(row["agent_run_key"])


def _sorted_usage_groups(groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for bucket in groups.values():
        items.append(
            {
                "label": str(bucket["label"]),
                "event_count": int(bucket["event_count"]),
                "total_tokens": int(bucket["total_tokens"]),
                "agent_run_count": len(bucket["agent_run_count"]),
            }
        )
    return sorted(items, key=lambda item: (-int(item["total_tokens"]), item["label"]))


def _count_mix(rows: list[dict[str, str | None]], key: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row[key])] += 1
    return [
        {"label": label, "count": counts[label]}
        for label in sorted(counts)
    ]


def _workspace_proxy(values: dict[str, str]) -> Any:
    class WorkspaceProxy:
        workspace_key = values["workspace_key"]
        display_label = values["display_label"]
        redacted_display_label = values["redacted_display_label"]
        resolved_root_path = values["resolved_root_path"]
        resolution_strategy = values["resolution_strategy"]

        @property
        def redacted_label(self) -> str:
            return self.redacted_display_label

    return WorkspaceProxy()
