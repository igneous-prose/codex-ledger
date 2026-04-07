from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from codex_ledger import __version__
from codex_ledger.domain.records import RedactionMode
from codex_ledger.normalize.privacy import DEFAULT_REDACTION_MODE, render_workspace_label
from codex_ledger.pricing.rules import (
    PricingRuleSet,
    list_rule_files,
    load_rule_file,
    load_rule_set,
)
from codex_ledger.storage.repository import fetch_workspace_alias_map


@dataclass(frozen=True)
class PricingContext:
    requested_rule_set_id: str | None
    selected_rule_set_id: str | None
    selection_mode: str
    rule_set: PricingRuleSet | None

    @property
    def included(self) -> bool:
        return self.rule_set is not None


def resolve_pricing_context(requested_rule_set_id: str | None) -> PricingContext:
    if requested_rule_set_id is not None:
        return PricingContext(
            requested_rule_set_id=requested_rule_set_id,
            selected_rule_set_id=requested_rule_set_id,
            selection_mode="explicit",
            rule_set=load_rule_set(requested_rule_set_id),
        )

    stable_rule_set = _latest_stable_rule_set()
    if stable_rule_set is None:
        return PricingContext(
            requested_rule_set_id=None,
            selected_rule_set_id=None,
            selection_mode="omitted_no_stable_rule_set",
            rule_set=None,
        )
    return PricingContext(
        requested_rule_set_id=None,
        selected_rule_set_id=stable_rule_set.rule_set_id,
        selection_mode="default_latest_stable",
        rule_set=stable_rule_set,
    )


def period_bounds(period: str, as_of: date) -> tuple[str, str]:
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


def fetch_alias_map(connection: sqlite3.Connection) -> dict[str, str]:
    return fetch_workspace_alias_map(connection)


def fetch_report_rows(
    connection: sqlite3.Connection,
    *,
    pricing_context: PricingContext,
    start_utc: str | None = None,
    end_utc: str | None = None,
    extra_where: tuple[str, ...] = (),
    extra_params: tuple[object, ...] = (),
) -> list[dict[str, Any]]:
    params: list[object] = [
        ""
        if pricing_context.selected_rule_set_id is None
        else pricing_context.selected_rule_set_id,
        ("" if pricing_context.rule_set is None else pricing_context.rule_set.pricing_plane),
    ]
    where_clauses = [
        "("
        "ue.total_tokens IS NOT NULL "
        "OR ue.input_tokens IS NOT NULL "
        "OR ue.cached_input_tokens IS NOT NULL "
        "OR ue.output_tokens IS NOT NULL"
        ")"
    ]
    if start_utc is not None:
        where_clauses.append("ue.event_ts_utc >= ?")
        params.append(start_utc)
    if end_utc is not None:
        where_clauses.append("ue.event_ts_utc < ?")
        params.append(end_utc)
    where_clauses.extend(extra_where)
    params.extend(extra_params)

    sql = f"""
        SELECT ue.event_id,
               ue.event_ts_utc,
               ue.event_index,
               ue.source_kind,
               ue.session_key,
               ps.raw_session_id,
               ps.originator,
               ue.raw_file_id,
               rf.stored_relpath,
               rf.source_kind,
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
               ue.total_tokens,
               ce.amount,
               ce.estimate_status,
               ce.explanation_json,
               ce.computed_at_utc
        FROM usage_events AS ue
        LEFT JOIN provider_sessions AS ps
          ON ps.session_key = ue.session_key
        JOIN raw_files AS rf
          ON rf.raw_file_id = ue.raw_file_id
        JOIN workspaces AS w
          ON w.workspace_key = ue.workspace_key
        LEFT JOIN agent_runs AS ar
          ON ar.agent_run_key = ue.agent_run_key
        LEFT JOIN cost_estimates AS ce
          ON ce.event_id = ue.event_id
         AND ce.rule_set_id = ?
         AND ce.pricing_plane = ?
        WHERE {" AND ".join(where_clauses)}
        ORDER BY ue.event_ts_utc, ue.event_index, ue.event_id
    """
    rows = connection.execute(sql, tuple(params)).fetchall()
    return [
        {
            "event_id": str(row[0]),
            "event_ts_utc": None if row[1] is None else str(row[1]),
            "event_date_utc": None if row[1] is None else str(row[1])[:10],
            "event_index": int(row[2]),
            "source_kind": str(row[3]),
            "session_key": None if row[4] is None else str(row[4]),
            "raw_session_id": None if row[5] is None else str(row[5]),
            "originator": None if row[6] is None else str(row[6]),
            "raw_file_id": str(row[7]),
            "stored_relpath": str(row[8]),
            "raw_file_source_kind": str(row[9]),
            "workspace_key": str(row[10]),
            "display_label": str(row[11]),
            "redacted_display_label": str(row[12]),
            "resolved_root_path": str(row[13]),
            "resolution_strategy": str(row[14]),
            "agent_run_key": None if row[15] is None else str(row[15]),
            "agent_kind": None if row[16] is None else str(row[16]),
            "agent_name": None if row[17] is None else str(row[17]),
            "agent_role": None if row[18] is None else str(row[18]),
            "requested_model_id": None if row[19] is None else str(row[19]),
            "observed_model_id": None if row[20] is None else str(row[20]),
            "lineage_status": None if row[21] is None else str(row[21]),
            "lineage_confidence": None if row[22] is None else str(row[22]),
            "input_tokens": int(row[23] or 0),
            "cached_input_tokens": int(row[24] or 0),
            "output_tokens": int(row[25] or 0),
            "reasoning_output_tokens": int(row[26] or 0),
            "total_tokens": int(row[27] or 0),
            "amount": None if row[28] is None else float(row[28]),
            "estimate_status": None if row[29] is None else str(row[29]),
            "explanation_json": None if row[30] is None else str(row[30]),
            "computed_at_utc": None if row[31] is None else str(row[31]),
        }
        for row in rows
    ]


def render_workspace_label_for_row(
    row: dict[str, Any],
    *,
    redaction_mode: RedactionMode = DEFAULT_REDACTION_MODE,
    alias_map: dict[str, str] | None = None,
) -> str:
    return render_workspace_label(
        _workspace_proxy(row),
        mode=redaction_mode,
        aliases=alias_map,
    )


def build_pricing_block(
    rows: list[dict[str, Any]],
    pricing_context: PricingContext,
) -> dict[str, Any]:
    if not pricing_context.included:
        return {
            "included": False,
            "selection_mode": pricing_context.selection_mode,
            "requested_rule_set_id": pricing_context.requested_rule_set_id,
            "selected_rule_set_id": None,
            "pricing_plane": None,
            "currency": None,
            "rule_set_version": None,
            "stability": None,
            "confidence": None,
            "coverage_status": "omitted",
            "reference_usd_estimate": None,
            "warnings": ["Cost omitted because no stable local pricing rule set was available."],
        }

    rule_set = pricing_context.rule_set
    assert rule_set is not None

    priced_event_count = 0
    unpriced_event_count = 0
    priced_token_total = 0
    unpriced_token_total = 0
    priced_amount_total = Decimal("0")
    status_counts: dict[str, int] = defaultdict(int)
    unsupported_by_model: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        status = row["estimate_status"] or "missing_estimate_row"
        status_counts[status] += 1
        if status == "priced":
            priced_event_count += 1
            priced_token_total += int(row["total_tokens"])
            priced_amount_total += Decimal(str(row["amount"] or 0.0))
            continue

        unpriced_event_count += 1
        unpriced_token_total += int(row["total_tokens"])
        key = (str(row["observed_model_id"] or "unknown"), _estimate_reason(row))
        bucket = unsupported_by_model.setdefault(
            key,
            {
                "model_id": key[0],
                "reason": key[1],
                "event_count": 0,
                "token_total": 0,
            },
        )
        bucket["event_count"] += 1
        bucket["token_total"] += int(row["total_tokens"])

    if not rows:
        coverage_status = "no_events"
    elif unpriced_event_count == 0:
        coverage_status = "full"
    elif priced_event_count == 0:
        coverage_status = "none"
    else:
        coverage_status = "partial"

    warnings: list[str] = []
    if coverage_status == "partial":
        warnings.append("Pricing coverage is partial; the USD estimate is incomplete.")
    elif coverage_status == "none" and rows:
        warnings.append("Pricing was selected but no matching priced events were found.")

    return {
        "included": True,
        "selection_mode": pricing_context.selection_mode,
        "requested_rule_set_id": pricing_context.requested_rule_set_id,
        "selected_rule_set_id": rule_set.rule_set_id,
        "pricing_plane": rule_set.pricing_plane,
        "currency": rule_set.currency,
        "rule_set_version": rule_set.version,
        "stability": rule_set.stability,
        "confidence": rule_set.confidence,
        "provenance": rule_set.provenance,
        "coverage_status": coverage_status,
        "priced_event_count": priced_event_count,
        "unpriced_event_count": unpriced_event_count,
        "priced_token_total": priced_token_total,
        "unpriced_token_total": unpriced_token_total,
        "reference_usd_estimate": _decimal_to_float(priced_amount_total),
        "estimate_status_mix": [
            {"label": label, "count": status_counts[label]} for label in sorted(status_counts)
        ],
        "unsupported_or_unknown_by_model": sorted(
            unsupported_by_model.values(),
            key=lambda item: (item["model_id"], item["reason"]),
        ),
        "warnings": warnings,
    }


def deterministic_generated_at_utc(
    rows: list[dict[str, Any]],
    *,
    fallback_utc: str,
) -> str:
    candidates = [fallback_utc]
    for row in rows:
        if row["event_ts_utc"] is not None:
            candidates.append(str(row["event_ts_utc"]))
        if row["computed_at_utc"] is not None:
            candidates.append(str(row["computed_at_utc"]))
    return max(candidates)


def base_payload(
    *,
    schema_version: str,
    rows: list[dict[str, Any]],
    filters: dict[str, Any],
    pricing: dict[str, Any],
    fallback_generated_at_utc: str,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "generated_at_utc": deterministic_generated_at_utc(
            rows,
            fallback_utc=fallback_generated_at_utc,
        ),
        "generator_version": __version__,
        "filters": filters,
        "timezone": "UTC",
        "pricing": pricing,
    }


def count_distinct(rows: list[dict[str, Any]], key: str) -> int:
    return len({row[key] for row in rows if row[key] is not None})


def summarize_token_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "event_count": len(rows),
        "total_tokens": sum(int(row["total_tokens"]) for row in rows),
        "input_tokens": sum(int(row["input_tokens"]) for row in rows),
        "cached_input_tokens": sum(int(row["cached_input_tokens"]) for row in rows),
        "output_tokens": sum(int(row["output_tokens"]) for row in rows),
        "reasoning_output_tokens": sum(int(row["reasoning_output_tokens"]) for row in rows),
    }


def sorted_counts(values: dict[str, int]) -> list[dict[str, Any]]:
    return [{"label": label, "count": values[label]} for label in sorted(values)]


def _latest_stable_rule_set() -> PricingRuleSet | None:
    candidates = []
    for path in list_rule_files():
        rule_set = load_rule_file(path)
        if rule_set.stability in {"experimental", "preview", "unstable"}:
            continue
        candidates.append(rule_set)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item.effective_from_utc or "",
            item.version,
            item.rule_set_id,
        ),
    )


def _estimate_reason(row: dict[str, Any]) -> str:
    value = row["explanation_json"]
    if not isinstance(value, str):
        return "missing_estimate_row"
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return "invalid_explanation_json"
    if isinstance(payload, dict):
        return str(payload.get("reason") or row["estimate_status"] or "missing_reason")
    return "invalid_explanation_json"


def _workspace_proxy(values: dict[str, Any]) -> Any:
    class WorkspaceProxy:
        workspace_key = str(values["workspace_key"])
        display_label = str(values["display_label"])
        redacted_display_label = str(values["redacted_display_label"])
        resolved_root_path = str(values["resolved_root_path"])
        resolution_strategy = str(values["resolution_strategy"])

        @property
        def redacted_label(self) -> str:
            return self.redacted_display_label

    return WorkspaceProxy()


def _decimal_to_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000000000001")))
