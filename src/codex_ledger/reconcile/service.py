from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from codex_ledger.reports.aggregate import build_aggregate_report


def reconcile_reference(
    archive_home: Path,
    *,
    input_path: Path,
    period: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Reference JSON must be an object")

    derived_period = period or _derive_period(payload)
    derived_as_of = as_of or _derive_as_of(payload)
    rule_set_id = _derive_rule_set(payload)
    current = build_aggregate_report(
        archive_home=archive_home,
        period=derived_period,
        as_of=derived_as_of,
        rule_set_id=rule_set_id,
    )
    reference_summary = _extract_summary(payload)
    current_summary = current["data"]["selected_period_totals"]

    diffs = []
    for key in sorted(reference_summary):
        if key not in current_summary:
            continue
        if reference_summary[key] != current_summary[key]:
            diffs.append(
                {
                    "field": key,
                    "reference": reference_summary[key],
                    "current": current_summary[key],
                }
            )

    return {
        "ok": not diffs,
        "reference_input": input_path.name,
        "derived_filters": {
            "period": derived_period,
            "as_of": derived_as_of.isoformat(),
            "rule_set_id": rule_set_id,
        },
        "diffs": diffs,
        "reference_summary": reference_summary,
        "current_summary": current_summary,
    }


def format_reconcile_table(payload: dict[str, Any]) -> str:
    lines = [
        f"Reconcile reference: {'ok' if payload['ok'] else 'failed'}",
        f"Input: {payload['reference_input']}",
        (
            "Derived filters: "
            f"{payload['derived_filters']['period']} "
            f"as of {payload['derived_filters']['as_of']}"
        ),
    ]
    if not payload["diffs"]:
        lines.append("- no diffs")
    else:
        for diff in payload["diffs"]:
            lines.append(
                f"- {diff['field']}: reference={diff['reference']} current={diff['current']}"
            )
    return "\n".join(lines)


def _derive_period(payload: dict[str, Any]) -> str:
    filters = payload.get("filters")
    if isinstance(filters, dict):
        period = filters.get("period")
        if isinstance(period, str):
            return period
    return "month"


def _derive_as_of(payload: dict[str, Any]) -> date:
    filters = payload.get("filters")
    if isinstance(filters, dict):
        value = filters.get("as_of")
        if isinstance(value, str):
            return date.fromisoformat(value)
        value = filters.get("date")
        if isinstance(value, str):
            return date.fromisoformat(value)
    return date.fromisoformat("1970-01-01")


def _derive_rule_set(payload: dict[str, Any]) -> str | None:
    pricing = payload.get("pricing")
    if isinstance(pricing, dict):
        value = pricing.get("selected_rule_set_id")
        if isinstance(value, str) and value != "":
            return value
    return None


def _extract_summary(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict):
        totals = data.get("selected_period_totals")
        if isinstance(totals, dict):
            return dict(totals)
    summary = payload.get("summary")
    if isinstance(summary, dict):
        return dict(summary)
    raise ValueError("Reference JSON does not contain a recognized summary block")
