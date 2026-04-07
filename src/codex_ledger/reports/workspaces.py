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

WORKSPACE_REPORT_SCHEMA_VERSION = "phase4-workspace-report-v1"


def build_workspace_report(
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
        rows = fetch_report_rows(
            connection,
            pricing_context=pricing_context,
            start_utc=start_utc,
            end_utc=end_utc,
        )
    pricing = build_pricing_block(rows, pricing_context)
    payload = base_payload(
        schema_version=WORKSPACE_REPORT_SCHEMA_VERSION,
        rows=rows,
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
    payload["data"] = _build_workspace_data(rows, pricing, redaction_mode, alias_map)
    return payload


def format_workspace_report_table(payload: dict[str, Any]) -> str:
    pricing = payload["pricing"]
    lines = [
        (
            "Workspace report: "
            f"{payload['filters']['period']} as of {payload['filters']['as_of']} "
            f"({payload['filters']['redaction_mode']})"
        )
    ]
    if pricing["included"]:
        lines.append(
            "Pricing: "
            f"{pricing['selected_rule_set_id']} "
            f"({pricing['coverage_status']}, "
            f"{pricing['reference_usd_estimate']} {pricing['currency']})"
        )
    else:
        lines.append(f"Pricing: omitted ({pricing['warnings'][0]})")
    for item in payload["data"]["workspaces"][:10]:
        line = (
            f"- {item['workspace_label']}: {item['total_tokens']} tokens, "
            f"sessions={item['session_count']}, agents={item['agent_run_count']}, "
            f"top_model={item['top_model']}"
        )
        if pricing["included"]:
            line += f", usd={item['reference_usd_estimate']}"
        lines.append(line)
    return "\n".join(lines)


def _build_workspace_data(
    rows: list[dict[str, Any]],
    pricing: dict[str, Any],
    redaction_mode: RedactionMode,
    alias_map: dict[str, str],
) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    model_tokens: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in rows:
        workspace_key = str(row["workspace_key"])
        label = render_workspace_label_for_row(
            row,
            redaction_mode=redaction_mode,
            alias_map=alias_map,
        )
        bucket = groups.setdefault(
            workspace_key,
            {
                "workspace_key": workspace_key,
                "workspace_label": label,
                "resolution_strategy": str(row["resolution_strategy"]),
                "event_count": 0,
                "session_keys": set(),
                "agent_run_keys": set(),
                "total_tokens": 0,
                "reasoning_output_tokens": 0,
                "priced_token_total": 0,
                "unpriced_token_total": 0,
                "reference_usd_estimate": 0.0,
                "first_seen_utc": row["event_ts_utc"],
                "last_seen_utc": row["event_ts_utc"],
            },
        )
        bucket["event_count"] += 1
        bucket["session_keys"].add(row["session_key"])
        if row["agent_run_key"] is not None:
            bucket["agent_run_keys"].add(row["agent_run_key"])
        bucket["total_tokens"] += int(row["total_tokens"])
        bucket["reasoning_output_tokens"] += int(row["reasoning_output_tokens"])
        if row["estimate_status"] == "priced":
            bucket["priced_token_total"] += int(row["total_tokens"])
            bucket["reference_usd_estimate"] += float(row["amount"] or 0.0)
        else:
            bucket["unpriced_token_total"] += int(row["total_tokens"])
        if row["event_ts_utc"] is not None and (
            bucket["first_seen_utc"] is None
            or str(row["event_ts_utc"]) < str(bucket["first_seen_utc"])
        ):
            bucket["first_seen_utc"] = row["event_ts_utc"]
        if row["event_ts_utc"] is not None and (
            bucket["last_seen_utc"] is None
            or str(row["event_ts_utc"]) > str(bucket["last_seen_utc"])
        ):
            bucket["last_seen_utc"] = row["event_ts_utc"]
        model_tokens[workspace_key][str(row["observed_model_id"] or "unknown")] += int(
            row["total_tokens"]
        )

    items = []
    for workspace_key, bucket in groups.items():
        top_model = max(
            model_tokens[workspace_key].items(),
            key=lambda item: (item[1], item[0]),
        )[0]
        item = {
            "workspace_key": workspace_key,
            "workspace_label": str(bucket["workspace_label"]),
            "resolution_strategy": str(bucket["resolution_strategy"]),
            "event_count": int(bucket["event_count"]),
            "session_count": len(bucket["session_keys"]),
            "agent_run_count": len(bucket["agent_run_keys"]),
            "total_tokens": int(bucket["total_tokens"]),
            "reasoning_output_tokens": int(bucket["reasoning_output_tokens"]),
            "top_model": top_model,
            "first_seen_utc": bucket["first_seen_utc"],
            "last_seen_utc": bucket["last_seen_utc"],
        }
        if pricing["included"]:
            priced = int(bucket["priced_token_total"])
            unpriced = int(bucket["unpriced_token_total"])
            item["priced_token_total"] = priced
            item["unpriced_token_total"] = unpriced
            item["reference_usd_estimate"] = float(bucket["reference_usd_estimate"])
            item["coverage_status"] = (
                "full" if unpriced == 0 else "partial" if priced > 0 else "none"
            )
        else:
            item["cost_status"] = "omitted"
        items.append(item)

    return {
        "workspaces": sorted(
            items,
            key=lambda item: (-int(item["total_tokens"]), str(item["workspace_label"])),
        )
    }
