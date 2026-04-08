from __future__ import annotations

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
)
from codex_ledger.storage.migrations import connect_database, default_database_path
from codex_ledger.utils.terminal import safe_terminal_field

EXPLAIN_REPORT_SCHEMA_VERSION = "phase4-explain-report-v1"


def explain_day(
    *,
    archive_home: Path,
    day: date,
    rule_set_id: str | None = None,
) -> dict[str, Any]:
    start_utc, end_utc = period_bounds("day", day)
    return _build_explain_report(
        archive_home=archive_home,
        filters={
            "kind": "day",
            "date": day.isoformat(),
            "start_utc": start_utc,
            "end_exclusive_utc": end_utc,
        },
        start_utc=start_utc,
        end_utc=end_utc,
        rule_set_id=rule_set_id,
    )


def explain_workspace(
    *,
    archive_home: Path,
    workspace_key: str,
    period: str,
    as_of: date,
    rule_set_id: str | None = None,
    redaction_mode: RedactionMode = DEFAULT_REDACTION_MODE,
) -> dict[str, Any]:
    start_utc, end_utc = period_bounds(period, as_of)
    return _build_explain_report(
        archive_home=archive_home,
        filters={
            "kind": "workspace",
            "workspace_key": workspace_key,
            "period": period,
            "as_of": as_of.isoformat(),
            "start_utc": start_utc,
            "end_exclusive_utc": end_utc,
            "redaction_mode": redaction_mode,
        },
        start_utc=start_utc,
        end_utc=end_utc,
        extra_where=("ue.workspace_key = ?",),
        extra_params=(workspace_key,),
        rule_set_id=rule_set_id,
        redaction_mode=redaction_mode,
    )


def explain_model(
    *,
    archive_home: Path,
    model_id: str,
    period: str,
    as_of: date,
    rule_set_id: str | None = None,
) -> dict[str, Any]:
    start_utc, end_utc = period_bounds(period, as_of)
    return _build_explain_report(
        archive_home=archive_home,
        filters={
            "kind": "model",
            "model_id": model_id,
            "period": period,
            "as_of": as_of.isoformat(),
            "start_utc": start_utc,
            "end_exclusive_utc": end_utc,
        },
        start_utc=start_utc,
        end_utc=end_utc,
        extra_where=("ue.model_id = ?",),
        extra_params=(model_id,),
        rule_set_id=rule_set_id,
    )


def format_explain_table(payload: dict[str, Any]) -> str:
    safe = safe_terminal_field
    summary = payload["summary"]
    pricing = payload["pricing"]
    lines = [
        f"Explain: {safe(payload['filters']['kind'])}",
        f"Events: {summary['event_count']}",
        f"Tokens: {summary['total_tokens']}",
        f"Sessions: {summary['session_count']}",
        f"Raw artifacts: {len(payload['source_artifacts'])}",
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
    lines.append("Source artifacts:")
    for item in payload["source_artifacts"][:5]:
        lines.append(
            f"- {safe(item['stored_relpath'])}: "
            f"{item['event_count']} events, {item['total_tokens']} tokens"
        )
    return "\n".join(lines)


def _build_explain_report(
    *,
    archive_home: Path,
    filters: dict[str, Any],
    start_utc: str | None,
    end_utc: str | None,
    rule_set_id: str | None,
    extra_where: tuple[str, ...] = (),
    extra_params: tuple[object, ...] = (),
    redaction_mode: RedactionMode = DEFAULT_REDACTION_MODE,
) -> dict[str, Any]:
    pricing_context = resolve_pricing_context(rule_set_id)
    database_path = default_database_path(archive_home)
    with connect_database(database_path) as connection:
        alias_map = fetch_alias_map(connection)
        rows = fetch_report_rows(
            connection,
            pricing_context=pricing_context,
            start_utc=start_utc,
            end_utc=end_utc,
            extra_where=extra_where,
            extra_params=extra_params,
        )
    pricing = build_pricing_block(rows, pricing_context)
    payload = base_payload(
        schema_version=EXPLAIN_REPORT_SCHEMA_VERSION,
        rows=rows,
        filters=filters,
        pricing=pricing,
        fallback_generated_at_utc=end_utc or "1970-01-01T00:00:00Z",
    )
    payload.update(_explain_data(rows, pricing, redaction_mode, alias_map))
    return payload


def _explain_data(
    rows: list[dict[str, Any]],
    pricing: dict[str, Any],
    redaction_mode: RedactionMode,
    alias_map: dict[str, str],
) -> dict[str, Any]:
    sessions: dict[str, dict[str, Any]] = {}
    agent_runs: dict[str, dict[str, Any]] = {}
    source_artifacts: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, Any]] = {}
    workspaces: dict[str, dict[str, Any]] = {}
    estimate_status_counts: dict[str, int] = defaultdict(int)
    events: list[dict[str, Any]] = []

    total_tokens = 0
    priced_tokens = 0
    unpriced_tokens = 0
    priced_amount = 0.0

    for row in rows:
        tokens = int(row["total_tokens"])
        total_tokens += tokens
        status = str(row["estimate_status"] or "missing_estimate_row")
        estimate_status_counts[status] += 1
        if status == "priced":
            priced_tokens += tokens
            priced_amount += float(row["amount"] or 0.0)
        else:
            unpriced_tokens += tokens

        session_key = str(row["session_key"] or "unknown-session")
        session_bucket = sessions.setdefault(
            session_key,
            {
                "session_key": session_key,
                "raw_session_id": row["raw_session_id"],
                "originator": row["originator"],
                "event_count": 0,
                "total_tokens": 0,
            },
        )
        session_bucket["event_count"] += 1
        session_bucket["total_tokens"] += tokens

        artifact_bucket = source_artifacts.setdefault(
            str(row["raw_file_id"]),
            {
                "raw_file_id": str(row["raw_file_id"]),
                "stored_relpath": str(row["stored_relpath"]),
                "source_kind": str(row["raw_file_source_kind"]),
                "event_count": 0,
                "total_tokens": 0,
            },
        )
        artifact_bucket["event_count"] += 1
        artifact_bucket["total_tokens"] += tokens

        model_label = str(row["observed_model_id"] or "unknown")
        model_bucket = models.setdefault(
            model_label,
            {"model_id": model_label, "event_count": 0, "total_tokens": 0},
        )
        model_bucket["event_count"] += 1
        model_bucket["total_tokens"] += tokens

        workspace_key = str(row["workspace_key"])
        workspace_bucket = workspaces.setdefault(
            workspace_key,
            {
                "workspace_key": workspace_key,
                "workspace_label": render_workspace_label_for_row(
                    row,
                    redaction_mode=redaction_mode,
                    alias_map=alias_map,
                ),
                "resolution_strategy": str(row["resolution_strategy"]),
                "event_count": 0,
                "total_tokens": 0,
            },
        )
        workspace_bucket["event_count"] += 1
        workspace_bucket["total_tokens"] += tokens

        if row["agent_run_key"] is not None:
            agent_bucket = agent_runs.setdefault(
                str(row["agent_run_key"]),
                {
                    "agent_run_key": str(row["agent_run_key"]),
                    "agent_kind": row["agent_kind"],
                    "agent_name": row["agent_name"],
                    "agent_role": row["agent_role"],
                    "requested_model_id": row["requested_model_id"],
                    "observed_model_id": row["observed_model_id"],
                    "lineage_status": row["lineage_status"],
                    "lineage_confidence": row["lineage_confidence"],
                    "event_count": 0,
                    "total_tokens": 0,
                },
            )
            agent_bucket["event_count"] += 1
            agent_bucket["total_tokens"] += tokens

        event_item = {
            "event_id": str(row["event_id"]),
            "event_ts_utc": row["event_ts_utc"],
            "event_index": int(row["event_index"]),
            "session_key": row["session_key"],
            "raw_file_id": str(row["raw_file_id"]),
            "stored_relpath": str(row["stored_relpath"]),
            "workspace_key": workspace_key,
            "workspace_label": render_workspace_label_for_row(
                row,
                redaction_mode=redaction_mode,
                alias_map=alias_map,
            ),
            "agent_run_key": row["agent_run_key"],
            "model_id": row["observed_model_id"],
            "total_tokens": tokens,
        }
        if pricing["included"]:
            event_item["estimate_status"] = status
            event_item["reference_usd_estimate"] = (
                None if row["amount"] is None else float(row["amount"])
            )
        events.append(event_item)

    summary: dict[str, Any] = {
        "event_count": len(rows),
        "session_count": len(sessions),
        "agent_run_count": len(agent_runs),
        "workspace_count": len(workspaces),
        "total_tokens": total_tokens,
    }
    if pricing["included"]:
        summary["priced_token_total"] = priced_tokens
        summary["unpriced_token_total"] = unpriced_tokens
        summary["reference_usd_estimate"] = priced_amount

    return {
        "summary": summary,
        "sessions": sorted(sessions.values(), key=lambda item: str(item["session_key"])),
        "agent_runs": sorted(agent_runs.values(), key=lambda item: str(item["agent_run_key"])),
        "source_artifacts": sorted(
            source_artifacts.values(),
            key=lambda item: str(item["stored_relpath"]),
        ),
        "models": sorted(
            models.values(), key=lambda item: (-int(item["total_tokens"]), item["model_id"])
        ),
        "workspace_attribution": sorted(
            workspaces.values(),
            key=lambda item: str(item["workspace_label"]),
        ),
        "estimate_status_mix": [
            {"label": label, "count": estimate_status_counts[label]}
            for label in sorted(estimate_status_counts)
        ],
        "events": events,
    }
