from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from codex_ledger.domain.records import RedactionMode
from codex_ledger.normalize.privacy import DEFAULT_REDACTION_MODE, render_workspace_label
from codex_ledger.pricing.rules import PricingRuleSet, load_rule_set, select_rule
from codex_ledger.storage.migrations import (
    apply_migrations,
    connect_database,
    default_database_path,
)
from codex_ledger.storage.repository import fetch_workspace_alias_map
from codex_ledger.utils.hashing import sha256_text
from codex_ledger.utils.json import canonical_json
from codex_ledger.utils.time import utc_now_iso

PRICING_COVERAGE_SCHEMA_VERSION = "phase3-pricing-coverage-v1"
ONE_MILLION = Decimal("1000000")
AMOUNT_QUANTUM = Decimal("0.000000000001")


@dataclass(frozen=True)
class PricingEvent:
    event_id: str
    provider: str
    source_kind: str
    event_ts_utc: str | None
    model_id: str | None
    requested_model_id: str | None
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    workspace_key: str
    agent_run_key: str | None
    agent_kind: str | None
    agent_name: str | None


@dataclass(frozen=True)
class CostEstimate:
    cost_estimate_id: str
    event_id: str
    rule_set_id: str
    pricing_plane: str
    currency: str
    amount: float | None
    confidence: str
    estimate_status: str
    explanation_json: str


def recalculate_event_costs(
    *,
    archive_home: Path,
    rule_set_id: str,
) -> dict[str, Any]:
    database_path = default_database_path(archive_home)
    apply_migrations(database_path)
    rule_set = load_rule_set(rule_set_id)

    with connect_database(database_path) as connection:
        _upsert_pricing_rule_set(connection, rule_set)
        events = _fetch_pricing_events(connection)
        status_counts: dict[str, int] = {}
        priced_amount_total = Decimal("0")
        inserted = 0
        updated = 0
        unchanged = 0

        for event in events:
            estimate = _estimate_event_cost(event, rule_set)
            outcome = _upsert_cost_estimate(connection, estimate)
            if outcome == "inserted":
                inserted += 1
            elif outcome == "updated":
                updated += 1
            else:
                unchanged += 1
            status_counts[estimate.estimate_status] = (
                status_counts.get(estimate.estimate_status, 0) + 1
            )
            if estimate.amount is not None:
                priced_amount_total += Decimal(str(estimate.amount))

    return {
        "rule_set_id": rule_set.rule_set_id,
        "pricing_plane": rule_set.pricing_plane,
        "currency": rule_set.currency,
        "event_count": len(events),
        "priced_amount_total": _decimal_to_float(priced_amount_total),
        "status_counts": status_counts,
        "inserted_count": inserted,
        "updated_count": updated,
        "unchanged_count": unchanged,
    }


def build_pricing_coverage(
    *,
    archive_home: Path,
    rule_set_id: str,
    redaction_mode: RedactionMode = DEFAULT_REDACTION_MODE,
) -> dict[str, Any]:
    database_path = default_database_path(archive_home)
    apply_migrations(database_path)

    with connect_database(database_path) as connection:
        alias_map = fetch_workspace_alias_map(connection)
        rule_set_row = connection.execute(
            """
            SELECT pricing_plane, currency, version
            FROM pricing_rule_sets
            WHERE rule_set_id = ?
            """,
            (rule_set_id,),
        ).fetchone()
        pricing_plane = str(rule_set_row[0]) if rule_set_row is not None else "reference_usd"
        currency = str(rule_set_row[1]) if rule_set_row is not None else "USD"
        version = str(rule_set_row[2]) if rule_set_row is not None else "unloaded"
        rows = _fetch_coverage_rows(connection, rule_set_id, pricing_plane)

    summary = {
        "priced_event_count": 0,
        "unpriced_event_count": 0,
        "priced_token_total": 0,
        "unpriced_token_total": 0,
        "priced_amount_total": 0.0,
    }
    unsupported_by_model: dict[tuple[str, str], dict[str, Any]] = {}
    workspace_groups: dict[str, dict[str, Any]] = {}
    model_groups: dict[str, dict[str, Any]] = {}
    agent_groups: dict[str, dict[str, Any]] = {}

    for row in rows:
        workspace_label = render_workspace_label(
            _workspace_proxy(row),
            mode=redaction_mode,
            aliases=alias_map,
        )
        priced = row["estimate_status"] == "priced"
        token_total = int(row["total_tokens"])
        if priced:
            summary["priced_event_count"] += 1
            summary["priced_token_total"] += token_total
            summary["priced_amount_total"] += float(row["amount"] or 0.0)
        else:
            summary["unpriced_event_count"] += 1
            summary["unpriced_token_total"] += token_total
            key = (str(row["model_id"] or "unknown"), str(row["reason"]))
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
            bucket["token_total"] += token_total

        workspace_bucket = workspace_groups.setdefault(
            str(row["workspace_key"]),
            {
                "workspace_key": str(row["workspace_key"]),
                "workspace_label": workspace_label,
                "priced_event_count": 0,
                "total_event_count": 0,
                "priced_token_total": 0,
                "total_token_total": 0,
                "priced_amount_total": 0.0,
            },
        )
        workspace_bucket["total_event_count"] += 1
        workspace_bucket["total_token_total"] += token_total
        if priced:
            workspace_bucket["priced_event_count"] += 1
            workspace_bucket["priced_token_total"] += token_total
            workspace_bucket["priced_amount_total"] += float(row["amount"] or 0.0)

        model_group_key = str(row["model_id"] or "unknown")
        model_bucket = model_groups.setdefault(
            model_group_key,
            {
                "model_id": model_group_key,
                "priced_event_count": 0,
                "total_event_count": 0,
                "priced_token_total": 0,
                "total_token_total": 0,
                "priced_amount_total": 0.0,
            },
        )
        model_bucket["total_event_count"] += 1
        model_bucket["total_token_total"] += token_total
        if priced:
            model_bucket["priced_event_count"] += 1
            model_bucket["priced_token_total"] += token_total
            model_bucket["priced_amount_total"] += float(row["amount"] or 0.0)

        agent_group_key = str(row["agent_run_key"] or "unassigned")
        agent_bucket = agent_groups.setdefault(
            agent_group_key,
            {
                "agent_run_key": agent_group_key,
                "agent_kind": row["agent_kind"] or "unknown",
                "agent_name": row["agent_name"] or "unknown",
                "priced_event_count": 0,
                "total_event_count": 0,
                "priced_token_total": 0,
                "total_token_total": 0,
                "priced_amount_total": 0.0,
            },
        )
        agent_bucket["total_event_count"] += 1
        agent_bucket["total_token_total"] += token_total
        if priced:
            agent_bucket["priced_event_count"] += 1
            agent_bucket["priced_token_total"] += token_total
            agent_bucket["priced_amount_total"] += float(row["amount"] or 0.0)

    summary["priced_amount_total"] = _decimal_to_float(Decimal(str(summary["priced_amount_total"])))

    return {
        "schema_version": PRICING_COVERAGE_SCHEMA_VERSION,
        "rule_set_id": rule_set_id,
        "pricing_plane": pricing_plane,
        "currency": currency,
        "rule_set_version": version,
        "summary": summary,
        "unsupported_or_unknown_by_model": sorted(
            unsupported_by_model.values(),
            key=lambda item: (item["model_id"], item["reason"]),
        ),
        "coverage_by_workspace": sorted(
            workspace_groups.values(),
            key=lambda item: item["workspace_label"],
        ),
        "coverage_by_model": sorted(
            model_groups.values(),
            key=lambda item: item["model_id"],
        ),
        "coverage_by_agent_run": sorted(
            agent_groups.values(),
            key=lambda item: item["agent_run_key"],
        ),
    }


def format_pricing_recalc_table(payload: dict[str, Any]) -> str:
    lines = [
        f"Rule set: {payload['rule_set_id']}",
        f"Pricing plane: {payload['pricing_plane']}",
        f"Events evaluated: {payload['event_count']}",
        f"Priced amount total: {payload['priced_amount_total']} {payload['currency']}",
        (
            "Upserts: "
            f"inserted={payload['inserted_count']}, "
            f"updated={payload['updated_count']}, "
            f"unchanged={payload['unchanged_count']}"
        ),
    ]
    for status, count in sorted(payload["status_counts"].items()):
        lines.append(f"- {status}: {count}")
    return "\n".join(lines)


def format_pricing_coverage_table(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        f"Pricing coverage: {payload['rule_set_id']}",
        (
            "Priced events: "
            f"{summary['priced_event_count']} "
            f"({summary['priced_token_total']} tokens)"
        ),
        (
            "Unpriced events: "
            f"{summary['unpriced_event_count']} "
            f"({summary['unpriced_token_total']} tokens)"
        ),
        f"Reference USD total: {summary['priced_amount_total']} {payload['currency']}",
        "Unsupported or unknown by model:",
    ]
    for item in payload["unsupported_or_unknown_by_model"][:5]:
        lines.append(
            f"- {item['model_id']} / {item['reason']}: "
            f"{item['event_count']} events, {item['token_total']} tokens"
        )
    return "\n".join(lines)


def _estimate_event_cost(event: PricingEvent, rule_set: PricingRuleSet) -> CostEstimate:
    selection = select_rule(
        rule_set=rule_set,
        provider=event.provider,
        model_id=event.model_id,
        event_ts_utc=event.event_ts_utc,
    )

    if selection.rule is None:
        explanation_json = canonical_json(
            {
                "provider": event.provider,
                "observed_model_id": event.model_id,
                "requested_model_id": event.requested_model_id,
                "reason": selection.reason,
                "status": selection.status,
                "source_kind": event.source_kind,
            }
        )
        return CostEstimate(
            cost_estimate_id=_cost_estimate_id(event.event_id, rule_set),
            event_id=event.event_id,
            rule_set_id=rule_set.rule_set_id,
            pricing_plane=rule_set.pricing_plane,
            currency=rule_set.currency,
            amount=None,
            confidence="unsupported",
            estimate_status=selection.status,
            explanation_json=explanation_json,
        )

    rule = selection.rule
    cached_tokens = (
        event.cached_input_tokens
        if rule_set.token_mapping.cached_input_tokens_field
        else 0
    )
    if rule_set.token_mapping.cached_input_behavior == "subtract_from_input":
        billable_input_tokens = max(event.input_tokens - cached_tokens, 0)
    else:
        billable_input_tokens = event.input_tokens

    billable_cached_tokens = cached_tokens if rule.cached_input_usd_per_1m is not None else 0
    amount = (
        (Decimal(billable_input_tokens) * rule.input_usd_per_1m / ONE_MILLION)
        + (
            Decimal(billable_cached_tokens)
            * (rule.cached_input_usd_per_1m or Decimal("0"))
            / ONE_MILLION
        )
        + (Decimal(event.output_tokens) * rule.output_usd_per_1m / ONE_MILLION)
    ).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)

    explanation_json = canonical_json(
        {
            "provider": event.provider,
            "observed_model_id": event.model_id,
            "requested_model_id": event.requested_model_id,
            "matched_rule_id": rule.rule_id,
            "rule_confidence": rule.confidence,
            "rule_stability": rule.stability,
            "raw_token_counts": {
                "input_tokens": event.input_tokens,
                "cached_input_tokens": event.cached_input_tokens,
                "output_tokens": event.output_tokens,
                "total_tokens": event.total_tokens,
            },
            "billable_token_counts": {
                "input_tokens": billable_input_tokens,
                "cached_input_tokens": billable_cached_tokens,
                "output_tokens": event.output_tokens,
            },
            "rates_per_1m": {
                "input_usd_per_1m": str(rule.input_usd_per_1m),
                "cached_input_usd_per_1m": (
                    None
                    if rule.cached_input_usd_per_1m is None
                    else str(rule.cached_input_usd_per_1m)
                ),
                "output_usd_per_1m": str(rule.output_usd_per_1m),
            },
            "cached_input_behavior": rule_set.token_mapping.cached_input_behavior,
            "status": "priced",
        }
    )
    return CostEstimate(
        cost_estimate_id=_cost_estimate_id(event.event_id, rule_set),
        event_id=event.event_id,
        rule_set_id=rule_set.rule_set_id,
        pricing_plane=rule_set.pricing_plane,
        currency=rule_set.currency,
        amount=_decimal_to_float(amount),
        confidence=rule.confidence,
        estimate_status="priced",
        explanation_json=explanation_json,
    )


def _fetch_pricing_events(connection: sqlite3.Connection) -> tuple[PricingEvent, ...]:
    rows = connection.execute(
        """
        SELECT ue.event_id,
               ue.provider,
               ue.source_kind,
               ue.event_ts_utc,
               ue.model_id,
               ar.requested_model_id,
               COALESCE(ue.input_tokens, 0),
               COALESCE(ue.cached_input_tokens, 0),
               COALESCE(ue.output_tokens, 0),
               COALESCE(ue.total_tokens, 0),
               ue.workspace_key,
               ue.agent_run_key,
               ar.agent_kind,
               ar.agent_name
        FROM usage_events AS ue
        LEFT JOIN agent_runs AS ar
          ON ar.agent_run_key = ue.agent_run_key
        WHERE ue.total_tokens IS NOT NULL
           OR ue.input_tokens IS NOT NULL
           OR ue.cached_input_tokens IS NOT NULL
           OR ue.output_tokens IS NOT NULL
        ORDER BY ue.event_id
        """
    ).fetchall()
    return tuple(
        PricingEvent(
            event_id=str(row[0]),
            provider=str(row[1]),
            source_kind=str(row[2]),
            event_ts_utc=None if row[3] is None else str(row[3]),
            model_id=None if row[4] is None else str(row[4]),
            requested_model_id=None if row[5] is None else str(row[5]),
            input_tokens=int(row[6]),
            cached_input_tokens=int(row[7]),
            output_tokens=int(row[8]),
            total_tokens=int(row[9]),
            workspace_key=str(row[10]),
            agent_run_key=None if row[11] is None else str(row[11]),
            agent_kind=None if row[12] is None else str(row[12]),
            agent_name=None if row[13] is None else str(row[13]),
        )
        for row in rows
    )


def _fetch_coverage_rows(
    connection: sqlite3.Connection,
    rule_set_id: str,
    pricing_plane: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT ue.event_id,
               ue.model_id,
               COALESCE(ue.total_tokens, 0),
               ue.workspace_key,
               w.display_label,
               w.redacted_display_label,
               w.resolved_root_path,
               w.resolution_strategy,
               ue.agent_run_key,
               ar.agent_kind,
               ar.agent_name,
               ce.amount,
               ce.estimate_status,
               ce.explanation_json
        FROM usage_events AS ue
        JOIN workspaces AS w
          ON w.workspace_key = ue.workspace_key
        LEFT JOIN agent_runs AS ar
          ON ar.agent_run_key = ue.agent_run_key
        LEFT JOIN cost_estimates AS ce
          ON ce.event_id = ue.event_id
         AND ce.rule_set_id = ?
         AND ce.pricing_plane = ?
        WHERE ue.total_tokens IS NOT NULL
           OR ue.input_tokens IS NOT NULL
           OR ue.cached_input_tokens IS NOT NULL
           OR ue.output_tokens IS NOT NULL
        ORDER BY ue.event_id
        """,
        (rule_set_id, pricing_plane),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        explanation = _parse_explanation(row[13])
        items.append(
            {
                "event_id": str(row[0]),
                "model_id": None if row[1] is None else str(row[1]),
                "total_tokens": int(row[2]),
                "workspace_key": str(row[3]),
                "display_label": str(row[4]),
                "redacted_display_label": str(row[5]),
                "resolved_root_path": str(row[6]),
                "resolution_strategy": str(row[7]),
                "agent_run_key": None if row[8] is None else str(row[8]),
                "agent_kind": None if row[9] is None else str(row[9]),
                "agent_name": None if row[10] is None else str(row[10]),
                "amount": None if row[11] is None else float(row[11]),
                "estimate_status": "missing_estimate" if row[12] is None else str(row[12]),
                "reason": str(explanation.get("reason") or "missing_estimate_row"),
            }
        )
    return items


def _upsert_pricing_rule_set(connection: sqlite3.Connection, rule_set: PricingRuleSet) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO pricing_rule_sets (
            rule_set_id,
            pricing_plane,
            version,
            effective_from_utc,
            effective_to_utc,
            currency,
            stability,
            source_hash,
            source_path,
            metadata_json,
            loaded_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rule_set_id) DO UPDATE SET
            pricing_plane = excluded.pricing_plane,
            version = excluded.version,
            effective_from_utc = excluded.effective_from_utc,
            effective_to_utc = excluded.effective_to_utc,
            currency = excluded.currency,
            stability = excluded.stability,
            source_hash = excluded.source_hash,
            source_path = excluded.source_path,
            metadata_json = excluded.metadata_json,
            updated_at_utc = excluded.updated_at_utc
        """,
        (
            rule_set.rule_set_id,
            rule_set.pricing_plane,
            rule_set.version,
            rule_set.effective_from_utc,
            rule_set.effective_to_utc,
            rule_set.currency,
            rule_set.stability,
            rule_set.source_hash,
            rule_set.source_path,
            canonical_json(
                {
                    "confidence": rule_set.confidence,
                    "provenance": rule_set.provenance,
                    "rule_count": len(rule_set.rules),
                }
            ),
            now,
            now,
        ),
    )


def _upsert_cost_estimate(connection: sqlite3.Connection, estimate: CostEstimate) -> str:
    existing = connection.execute(
        """
        SELECT cost_estimate_id,
               amount,
               confidence,
               estimate_status,
               explanation_json
        FROM cost_estimates
        WHERE event_id = ?
          AND rule_set_id = ?
          AND pricing_plane = ?
        """,
        (estimate.event_id, estimate.rule_set_id, estimate.pricing_plane),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO cost_estimates (
                cost_estimate_id,
                event_id,
                rule_set_id,
                pricing_plane,
                currency,
                amount,
                confidence,
                estimate_status,
                explanation_json,
                computed_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                estimate.cost_estimate_id,
                estimate.event_id,
                estimate.rule_set_id,
                estimate.pricing_plane,
                estimate.currency,
                estimate.amount,
                estimate.confidence,
                estimate.estimate_status,
                estimate.explanation_json,
                utc_now_iso(),
            ),
        )
        return "inserted"

    existing_signature = (
        None if existing[1] is None else float(existing[1]),
        str(existing[2]),
        str(existing[3]),
        str(existing[4]),
    )
    new_signature = (
        estimate.amount,
        estimate.confidence,
        estimate.estimate_status,
        estimate.explanation_json,
    )
    if existing_signature == new_signature:
        return "unchanged"

    connection.execute(
        """
        UPDATE cost_estimates
        SET currency = ?,
            amount = ?,
            confidence = ?,
            estimate_status = ?,
            explanation_json = ?,
            computed_at_utc = ?
        WHERE event_id = ?
          AND rule_set_id = ?
          AND pricing_plane = ?
        """,
        (
            estimate.currency,
            estimate.amount,
            estimate.confidence,
            estimate.estimate_status,
            estimate.explanation_json,
            utc_now_iso(),
            estimate.event_id,
            estimate.rule_set_id,
            estimate.pricing_plane,
        ),
    )
    return "updated"


def _cost_estimate_id(event_id: str, rule_set: PricingRuleSet) -> str:
    return sha256_text(f"{event_id}:{rule_set.rule_set_id}:{rule_set.pricing_plane}")[:32]


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


def _parse_explanation(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _decimal_to_float(value: Decimal) -> float:
    return float(value.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP))
