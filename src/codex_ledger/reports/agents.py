from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from codex_ledger.domain.records import RedactionMode
from codex_ledger.normalize.privacy import DEFAULT_REDACTION_MODE
from codex_ledger.reports.common import (
    base_payload,
    build_pricing_block,
    fetch_alias_map,
    fetch_report_rows,
    period_bounds,
    render_workspace_label_for_row,
    resolve_pricing_context,
    summarize_token_totals,
)
from codex_ledger.storage.migrations import connect_database, default_database_path
from codex_ledger.utils.terminal import safe_terminal_field

DIAGNOSTIC_SCHEMA_VERSION = "phase2.1-agent-diagnostics-v1"


def build_agent_report(
    *,
    archive_home: Path,
    period: str,
    as_of: date,
    rule_set_id: str | None = None,
    redaction_mode: RedactionMode = DEFAULT_REDACTION_MODE,
) -> dict[str, Any]:
    start_utc, end_utc = period_bounds(period, as_of)
    pricing_context = resolve_pricing_context(rule_set_id)
    database_path = default_database_path(archive_home)
    with connect_database(database_path) as connection:
        alias_map = fetch_alias_map(connection)
        token_rows = fetch_report_rows(
            connection,
            pricing_context=pricing_context,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        activity_rows = _merge_pricing_fields(
            _fetch_agent_activity_rows(connection, start_utc=start_utc, end_utc=end_utc),
            token_rows,
        )
        lineage_rows = _fetch_agent_runs(connection, start_utc, end_utc)
    pricing = build_pricing_block(token_rows, pricing_context)
    payload = base_payload(
        schema_version=DIAGNOSTIC_SCHEMA_VERSION,
        rows=token_rows,
        filters={
            "period": period,
            "as_of": as_of.isoformat(),
            "start_utc": start_utc,
            "end_exclusive_utc": end_utc,
            "redaction_mode": redaction_mode,
        },
        pricing=pricing,
        fallback_generated_at_utc=end_utc,
    )
    payload.update(
        _agent_report_data(activity_rows, lineage_rows, pricing, redaction_mode, alias_map)
    )
    return payload


def explain_agent_run(
    *,
    archive_home: Path,
    agent_run_key: str,
    rule_set_id: str | None = None,
    redaction_mode: RedactionMode = DEFAULT_REDACTION_MODE,
) -> dict[str, Any]:
    pricing_context = resolve_pricing_context(rule_set_id)
    database_path = default_database_path(archive_home)
    with connect_database(database_path) as connection:
        alias_map = fetch_alias_map(connection)
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
        token_rows = fetch_report_rows(
            connection,
            pricing_context=pricing_context,
            extra_where=("ue.agent_run_key = ?",),
            extra_params=(agent_run_key,),
        )
        activity_rows = _merge_pricing_fields(
            _fetch_agent_activity_rows(connection, agent_run_key=agent_run_key),
            token_rows,
        )

    pricing = build_pricing_block(token_rows, pricing_context)
    payload = base_payload(
        schema_version=DIAGNOSTIC_SCHEMA_VERSION,
        rows=token_rows,
        filters={
            "kind": "agent",
            "agent_run_key": agent_run_key,
            "redaction_mode": redaction_mode,
        },
        pricing=pricing,
        fallback_generated_at_utc=str(row[13] or row[14] or "1970-01-01T00:00:00Z"),
    )

    workspace_items: dict[str, dict[str, str]] = {}
    observed_model_ids: set[str] = set()
    estimate_status_counts: dict[str, int] = defaultdict(int)
    total_tokens = 0
    priced_tokens = 0
    unpriced_tokens = 0
    priced_amount = 0.0
    events = []
    for event in activity_rows:
        workspace_label = render_workspace_label_for_row(
            event,
            redaction_mode=redaction_mode,
            alias_map=alias_map,
        )
        workspace_items[str(event["workspace_key"])] = {
            "workspace_key": str(event["workspace_key"]),
            "workspace_label": workspace_label,
            "resolution_strategy": str(event["resolution_strategy"]),
        }
        if event["observed_model_id"] is not None:
            observed_model_ids.add(str(event["observed_model_id"]))
        status = str(event["estimate_status"] or "missing_estimate_row")
        estimate_status_counts[status] += 1
        total_tokens += int(event["total_tokens"])
        if status == "priced":
            priced_tokens += int(event["total_tokens"])
            priced_amount += float(event["amount"] or 0.0)
        else:
            unpriced_tokens += int(event["total_tokens"])
        event_item = {
            "event_id": str(event["event_id"]),
            "event_ts_utc": event["event_ts_utc"],
            "event_index": int(event["event_index"]),
            "model_id": event["observed_model_id"],
            "workspace_label": workspace_label,
            "stored_relpath": str(event["stored_relpath"]),
            "total_tokens": int(event["total_tokens"]),
        }
        if pricing["included"]:
            event_item["estimate_status"] = status
            event_item["reference_usd_estimate"] = (
                None if event["amount"] is None else float(event["amount"])
            )
        events.append(event_item)

    payload.update(
        {
            "agent_run": {
                "agent_run_key": str(row[0]),
                "session_key": str(row[1]),
                "lineage_key": str(row[2]),
                "parent_agent_run_key": None if row[3] is None else str(row[3]),
                "raw_parent_agent_run_id": None if row[4] is None else str(row[4]),
                "agent_kind": str(row[5]),
                "agent_name": None if row[6] is None else str(row[6]),
                "agent_role": None if row[7] is None else str(row[7]),
                "requested_model_id": None if row[8] is None else str(row[8]),
                "observed_model_id": None if row[9] is None else str(row[9]),
                "lineage_status": str(row[10]),
                "lineage_confidence": str(row[11]),
                "unresolved_reason": None if row[12] is None else str(row[12]),
                "started_at_utc": None if row[13] is None else str(row[13]),
                "ended_at_utc": None if row[14] is None else str(row[14]),
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
                "event_count": len(activity_rows),
                "total_tokens": total_tokens,
                "observed_model_ids": sorted(observed_model_ids),
                **(
                    {
                        "priced_token_total": priced_tokens,
                        "unpriced_token_total": unpriced_tokens,
                        "reference_usd_estimate": priced_amount,
                        "estimate_status_mix": [
                            {"label": label, "count": estimate_status_counts[label]}
                            for label in sorted(estimate_status_counts)
                        ],
                    }
                    if pricing["included"]
                    else {}
                ),
            },
            "workspace_attribution": [workspace_items[key] for key in sorted(workspace_items)],
            "events": events,
        }
    )
    return payload


def format_agent_report_table(payload: dict[str, Any]) -> str:
    safe = safe_terminal_field
    summary = payload["summary"]
    pricing = payload["pricing"]
    lines = [
        (
            f"Agent diagnostics: {safe(payload['filters']['period'])} "
            f"as of {safe(payload['filters']['as_of'])}"
        ),
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
    ]
    if pricing["included"]:
        lines.append(
            "Pricing: "
            f"{safe(pricing['selected_rule_set_id'])} "
            f"({safe(pricing['coverage_status'])}, "
            f"{pricing['reference_usd_estimate']} {safe(pricing['currency'])})"
        )
    else:
        lines.append(f"Pricing: omitted ({safe(pricing['warnings'][0])})")
    lines.append("Top heavy hitters:")
    for item in payload["top_heavy_hitters"][:5]:
        label = safe(item["agent_name"] or item["agent_role"] or item["agent_run_key"])
        suffix = ""
        if pricing["included"]:
            suffix = f", usd={item['reference_usd_estimate']}"
        lines.append(
            f"- {label}: {item['total_tokens']} tokens across {item['event_count']} events{suffix}"
        )
    return "\n".join(lines)


def format_agent_explain_table(payload: dict[str, Any]) -> str:
    safe = safe_terminal_field
    run = payload["agent_run"]
    summary = payload["event_summary"]
    pricing = payload["pricing"]
    lines = [
        f"Agent run: {safe(run['agent_run_key'])}",
        f"Kind: {safe(run['agent_kind'])}",
        f"Name: {safe(run['agent_name'] or 'unknown')}",
        f"Role: {safe(run['agent_role'] or 'unknown')}",
        (f"Lineage: {safe(run['lineage_status'])} ({safe(run['lineage_confidence'])})"),
        f"Requested model: {safe(run['requested_model_id'] or 'unknown')}",
        f"Observed model: {safe(run['observed_model_id'] or 'unknown')}",
        f"Events: {summary['event_count']}",
        f"Tokens: {summary['total_tokens']}",
        f"Stored raw artifact: {safe(payload['provenance']['stored_relpath'])}",
    ]
    if pricing["included"]:
        lines.append(
            "Pricing: "
            f"{safe(pricing['selected_rule_set_id'])} "
            f"({summary['reference_usd_estimate']} {safe(pricing['currency'])})"
        )
    return "\n".join(lines)


def _agent_report_data(
    rows: list[dict[str, Any]],
    lineage_rows: list[dict[str, str | None]],
    pricing: dict[str, Any],
    redaction_mode: RedactionMode,
    alias_map: dict[str, str],
) -> dict[str, Any]:
    root_rows = [row for row in rows if row["agent_kind"] == "root"]
    subagent_rows = [row for row in rows if row["agent_kind"] != "root"]
    root_usage = summarize_token_totals(root_rows)
    subagent_usage = summarize_token_totals(subagent_rows)

    usage_by_name = _group_rows(rows, "agent_name")
    usage_by_role = _group_rows(rows, "agent_role")
    usage_by_requested_model = _group_rows(rows, "requested_model_id")
    usage_by_observed_model = _group_rows(rows, "observed_model_id")
    heavy_hitters = _heavy_hitters(rows, redaction_mode, alias_map)

    workspace_spread: dict[str, set[str]] = defaultdict(set)
    workspace_labels_by_key: dict[str, str] = {}
    for row in rows:
        agent_name = str(row["agent_name"] or "unknown")
        workspace_spread[agent_name].add(str(row["workspace_key"]))
        workspace_labels_by_key[str(row["workspace_key"])] = render_workspace_label_for_row(
            row,
            redaction_mode=redaction_mode,
            alias_map=alias_map,
        )

    return {
        "summary": {
            "root_usage": root_usage,
            "subagent_usage": subagent_usage,
            "matched_child_count": sum(
                1
                for row in lineage_rows
                if row["agent_kind"] == "subagent"
                and row["lineage_key"] == "session"
                and row["lineage_status"] == "resolved"
            ),
            "unresolved_spawn_count": sum(
                1 for row in lineage_rows if row["lineage_status"] == "spawn_only_unmatched"
            ),
            "orphan_child_count": sum(
                1 for row in lineage_rows if row["lineage_status"] == "child_only_orphaned"
            ),
            "lineage_status_mix": _count_mix(lineage_rows, "lineage_status"),
            "lineage_confidence_mix": _count_mix(lineage_rows, "lineage_confidence"),
        },
        "usage_by_agent_name": usage_by_name,
        "usage_by_agent_role": usage_by_role,
        "usage_by_requested_model": usage_by_requested_model,
        "usage_by_observed_model": usage_by_observed_model,
        "top_heavy_hitters": heavy_hitters,
        "top_priced_heavy_hitters": (
            sorted(
                heavy_hitters,
                key=lambda item: (
                    -float(item["reference_usd_estimate"]),
                    -int(item["total_tokens"]),
                    str(item["agent_run_key"]),
                ),
            )
            if pricing["included"]
            else []
        ),
        "workspace_spread_by_agent": [
            {
                "agent_name": agent_name,
                "workspace_count": len(workspace_keys),
                "workspace_labels": sorted(workspace_labels_by_key[key] for key in workspace_keys),
            }
            for agent_name, workspace_keys in sorted(workspace_spread.items())
        ],
    }


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
               unresolved_reason
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
        }
        for row in rows
    ]


def _fetch_agent_activity_rows(
    connection: sqlite3.Connection,
    *,
    start_utc: str | None = None,
    end_utc: str | None = None,
    agent_run_key: str | None = None,
) -> list[dict[str, Any]]:
    where_clauses = ["ue.agent_run_key IS NOT NULL"]
    params: list[object] = []
    if start_utc is not None:
        where_clauses.append("ue.event_ts_utc >= ?")
        params.append(start_utc)
    if end_utc is not None:
        where_clauses.append("ue.event_ts_utc < ?")
        params.append(end_utc)
    if agent_run_key is not None:
        where_clauses.append("ue.agent_run_key = ?")
        params.append(agent_run_key)

    rows = connection.execute(
        f"""
        SELECT ue.event_id,
               ue.event_ts_utc,
               ue.event_index,
               ue.session_key,
               ue.raw_file_id,
               rf.stored_relpath,
               ue.workspace_key,
               w.display_label,
               w.redacted_display_label,
               w.resolved_root_path,
               w.resolution_strategy,
               ue.agent_run_key,
               ar.agent_kind,
               ar.agent_name,
               ar.agent_role,
               ar.requested_model_id,
               ue.model_id,
               ar.lineage_status,
               ar.lineage_confidence,
               ue.input_tokens,
               ue.cached_input_tokens,
               ue.output_tokens,
               ue.reasoning_output_tokens,
               ue.total_tokens
        FROM usage_events AS ue
        JOIN raw_files AS rf
          ON rf.raw_file_id = ue.raw_file_id
        JOIN workspaces AS w
          ON w.workspace_key = ue.workspace_key
        JOIN agent_runs AS ar
          ON ar.agent_run_key = ue.agent_run_key
        WHERE {" AND ".join(where_clauses)}
        ORDER BY ue.event_ts_utc, ue.event_index, ue.event_id
        """,
        tuple(params),
    ).fetchall()
    return [
        {
            "event_id": str(row[0]),
            "event_ts_utc": None if row[1] is None else str(row[1]),
            "event_index": int(row[2]),
            "session_key": None if row[3] is None else str(row[3]),
            "raw_file_id": str(row[4]),
            "stored_relpath": str(row[5]),
            "workspace_key": str(row[6]),
            "display_label": str(row[7]),
            "redacted_display_label": str(row[8]),
            "resolved_root_path": str(row[9]),
            "resolution_strategy": str(row[10]),
            "agent_run_key": str(row[11]),
            "agent_kind": str(row[12]),
            "agent_name": None if row[13] is None else str(row[13]),
            "agent_role": None if row[14] is None else str(row[14]),
            "requested_model_id": None if row[15] is None else str(row[15]),
            "observed_model_id": None if row[16] is None else str(row[16]),
            "lineage_status": None if row[17] is None else str(row[17]),
            "lineage_confidence": None if row[18] is None else str(row[18]),
            "input_tokens": int(row[19] or 0),
            "cached_input_tokens": int(row[20] or 0),
            "output_tokens": int(row[21] or 0),
            "reasoning_output_tokens": int(row[22] or 0),
            "total_tokens": int(row[23] or 0),
            "estimate_status": None,
            "amount": None,
        }
        for row in rows
    ]


def _merge_pricing_fields(
    activity_rows: list[dict[str, Any]],
    token_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pricing_by_event = {
        str(row["event_id"]): {
            "estimate_status": row["estimate_status"],
            "amount": row["amount"],
        }
        for row in token_rows
    }
    merged = []
    for row in activity_rows:
        merged_row = dict(row)
        merged_row.update(pricing_by_event.get(str(row["event_id"]), {}))
        merged.append(merged_row)
    return merged


def _group_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = str(row[key] or "unknown")
        bucket = groups.setdefault(
            label,
            {
                "label": label,
                "event_count": 0,
                "total_tokens": 0,
                "priced_token_total": 0,
                "unpriced_token_total": 0,
                "reference_usd_estimate": 0.0,
                "agent_run_keys": set(),
                "session_keys": set(),
                "workspace_keys": set(),
            },
        )
        bucket["event_count"] += 1
        bucket["total_tokens"] += int(row["total_tokens"])
        if row["estimate_status"] == "priced":
            bucket["priced_token_total"] += int(row["total_tokens"])
            bucket["reference_usd_estimate"] += float(row["amount"] or 0.0)
        else:
            bucket["unpriced_token_total"] += int(row["total_tokens"])
        if row["agent_run_key"] is not None:
            bucket["agent_run_keys"].add(row["agent_run_key"])
        if row["session_key"] is not None:
            bucket["session_keys"].add(row["session_key"])
        bucket["workspace_keys"].add(row["workspace_key"])
    items = []
    for bucket in groups.values():
        items.append(
            {
                "label": str(bucket["label"]),
                "event_count": int(bucket["event_count"]),
                "total_tokens": int(bucket["total_tokens"]),
                "priced_token_total": int(bucket["priced_token_total"]),
                "unpriced_token_total": int(bucket["unpriced_token_total"]),
                "reference_usd_estimate": float(bucket["reference_usd_estimate"]),
                "agent_run_count": len(bucket["agent_run_keys"]),
                "session_count": len(bucket["session_keys"]),
                "workspace_count": len(bucket["workspace_keys"]),
            }
        )
    return sorted(items, key=lambda item: (-int(item["total_tokens"]), str(item["label"])))


def _heavy_hitters(
    rows: list[dict[str, Any]],
    redaction_mode: RedactionMode,
    alias_map: dict[str, str],
) -> list[dict[str, Any]]:
    heavy_hitters: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["agent_run_key"] or "unassigned")
        bucket = heavy_hitters.setdefault(
            key,
            {
                "agent_run_key": key,
                "agent_name": row["agent_name"],
                "agent_role": row["agent_role"],
                "agent_kind": row["agent_kind"] or "unknown",
                "lineage_status": row["lineage_status"],
                "requested_model_id": row["requested_model_id"],
                "observed_model_id": row["observed_model_id"],
                "event_count": 0,
                "total_tokens": 0,
                "priced_token_total": 0,
                "unpriced_token_total": 0,
                "reference_usd_estimate": 0.0,
                "workspace_labels": set(),
                "session_keys": set(),
            },
        )
        bucket["event_count"] += 1
        bucket["total_tokens"] += int(row["total_tokens"])
        if row["estimate_status"] == "priced":
            bucket["priced_token_total"] += int(row["total_tokens"])
            bucket["reference_usd_estimate"] += float(row["amount"] or 0.0)
        else:
            bucket["unpriced_token_total"] += int(row["total_tokens"])
        bucket["workspace_labels"].add(
            render_workspace_label_for_row(
                row,
                redaction_mode=redaction_mode,
                alias_map=alias_map,
            )
        )
        if row["session_key"] is not None:
            bucket["session_keys"].add(row["session_key"])
    items = []
    for bucket in heavy_hitters.values():
        items.append(
            {
                "agent_run_key": str(bucket["agent_run_key"]),
                "agent_name": bucket["agent_name"],
                "agent_role": bucket["agent_role"],
                "agent_kind": str(bucket["agent_kind"]),
                "lineage_status": bucket["lineage_status"],
                "requested_model_id": bucket["requested_model_id"],
                "observed_model_id": bucket["observed_model_id"],
                "event_count": int(bucket["event_count"]),
                "total_tokens": int(bucket["total_tokens"]),
                "priced_token_total": int(bucket["priced_token_total"]),
                "unpriced_token_total": int(bucket["unpriced_token_total"]),
                "reference_usd_estimate": float(bucket["reference_usd_estimate"]),
                "workspace_count": len(bucket["workspace_labels"]),
                "workspace_labels": sorted(bucket["workspace_labels"]),
                "session_count": len(bucket["session_keys"]),
            }
        )
    return sorted(
        items,
        key=lambda item: (-int(item["total_tokens"]), str(item["agent_run_key"])),
    )


def _count_mix(rows: list[dict[str, str | None]], key: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row[key])] += 1
    return [{"label": label, "count": counts[label]} for label in sorted(counts)]
