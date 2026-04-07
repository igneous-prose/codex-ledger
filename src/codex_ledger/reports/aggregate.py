from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from codex_ledger.reports.common import (
    base_payload,
    build_pricing_block,
    count_distinct,
    fetch_report_rows,
    period_bounds,
    resolve_pricing_context,
    summarize_token_totals,
)
from codex_ledger.storage.migrations import connect_database, default_database_path

AGGREGATE_REPORT_SCHEMA_VERSION = "phase4-aggregate-report-v1"


def build_aggregate_report(
    *,
    archive_home: Path,
    period: str,
    as_of: date,
    rule_set_id: str | None = None,
) -> dict[str, Any]:
    start_utc, end_utc = period_bounds(period, as_of)
    pricing_context = resolve_pricing_context(rule_set_id)
    database_path = default_database_path(archive_home)
    with connect_database(database_path) as connection:
        rows = fetch_report_rows(
            connection,
            pricing_context=pricing_context,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        workspace_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM workspaces
                WHERE first_seen_at_utc < ?
                  AND last_seen_at_utc >= ?
                """,
                (end_utc, start_utc),
            ).fetchone()[0]
        )
    pricing = build_pricing_block(rows, pricing_context)
    payload = base_payload(
        schema_version=AGGREGATE_REPORT_SCHEMA_VERSION,
        rows=rows,
        filters={
            "period": period,
            "as_of": as_of.isoformat(),
            "start_utc": start_utc,
            "end_exclusive_utc": end_utc,
        },
        pricing=pricing,
        fallback_generated_at_utc=end_utc,
    )
    payload["data"] = _build_aggregate_data(rows, pricing, workspace_count=workspace_count)
    return payload


def format_aggregate_report_table(payload: dict[str, Any]) -> str:
    totals = payload["data"]["selected_period_totals"]
    pricing = payload["pricing"]
    lines = [
        (f"Aggregate report: {payload['filters']['period']} as of {payload['filters']['as_of']}"),
        f"Events: {totals['event_count']}",
        f"Tokens: {totals['total_tokens']}",
        f"Workspaces: {totals['workspace_count']}",
        f"Models: {len(payload['data']['totals_by_model'])}",
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
    lines.append("Top models:")
    for item in payload["data"]["totals_by_model"][:5]:
        lines.append(f"- {item['model_id']}: {item['total_tokens']} tokens")
    return "\n".join(lines)


def _build_aggregate_data(
    rows: list[dict[str, Any]],
    pricing: dict[str, Any],
    *,
    workspace_count: int,
) -> dict[str, Any]:
    totals: dict[str, Any] = summarize_token_totals(rows)
    totals["workspace_count"] = workspace_count
    totals["session_count"] = count_distinct(rows, "session_key")
    totals["agent_run_count"] = count_distinct(rows, "agent_run_key")
    if pricing["included"]:
        totals["priced_token_total"] = int(pricing["priced_token_total"])
        totals["unpriced_token_total"] = int(pricing["unpriced_token_total"])
        totals["reference_usd_estimate"] = pricing["reference_usd_estimate"]
        totals["pricing_coverage_status"] = pricing["coverage_status"]
    else:
        totals["cost_status"] = "omitted"

    model_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    originator_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bucket_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        model_groups[str(row["observed_model_id"] or "unknown")].append(row)
        if row["originator"] not in {None, "", "Desktop", "Imported"}:
            originator_groups[str(row["originator"])].append(row)
        bucket_groups[str(row["event_date_utc"])].append(row)

    period_buckets = []
    for bucket_date in sorted(bucket_groups):
        bucket_rows = bucket_groups[bucket_date]
        bucket_models = _sorted_group_totals(
            bucket_rows, key="observed_model_id", label_key="model_id"
        )
        item = {
            "date": bucket_date,
            "event_count": len(bucket_rows),
            "total_tokens": sum(int(row["total_tokens"]) for row in bucket_rows),
            "top_models": bucket_models[:3],
        }
        if pricing["included"]:
            item["priced_token_total"] = sum(
                int(row["total_tokens"])
                for row in bucket_rows
                if row["estimate_status"] == "priced"
            )
            item["unpriced_token_total"] = sum(
                int(row["total_tokens"])
                for row in bucket_rows
                if row["estimate_status"] != "priced"
            )
            item["reference_usd_estimate"] = sum(
                float(row["amount"] or 0.0)
                for row in bucket_rows
                if row["estimate_status"] == "priced"
            )
        period_buckets.append(item)

    data: dict[str, Any] = {
        "selected_period_totals": totals,
        "period_buckets": period_buckets,
        "totals_by_model": _sorted_group_totals(
            rows, key="observed_model_id", label_key="model_id"
        ),
        "totals_by_account": [
            {
                "account_label": account,
                "event_count": len(account_rows),
                "total_tokens": sum(int(row["total_tokens"]) for row in account_rows),
            }
            for account, account_rows in sorted(originator_groups.items())
        ],
        "workspace_count": totals["workspace_count"],
        "top_models_by_day": [
            {
                "date": item["date"],
                "top_models": item["top_models"],
            }
            for item in period_buckets
        ],
    }
    if pricing["included"] and pricing["unsupported_or_unknown_by_model"]:
        data["unsupported_or_unknown_models"] = pricing["unsupported_or_unknown_by_model"]
    return data


def _sorted_group_totals(
    rows: list[dict[str, Any]],
    *,
    key: str,
    label_key: str,
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = str(row[key] or "unknown")
        bucket = groups.setdefault(
            label,
            {
                label_key: label,
                "event_count": 0,
                "total_tokens": 0,
            },
        )
        bucket["event_count"] += 1
        bucket["total_tokens"] += int(row["total_tokens"])
        if row["estimate_status"] == "priced":
            bucket["priced_token_total"] = int(bucket.get("priced_token_total", 0)) + int(
                row["total_tokens"]
            )
            bucket["reference_usd_estimate"] = float(
                bucket.get("reference_usd_estimate", 0.0)
            ) + float(row["amount"] or 0.0)
    return sorted(
        groups.values(),
        key=lambda item: (-int(item["total_tokens"]), str(item[label_key])),
    )
