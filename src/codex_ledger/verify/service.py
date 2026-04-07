from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from codex_ledger.reports.agents import build_agent_report
from codex_ledger.reports.aggregate import build_aggregate_report
from codex_ledger.reports.schema import ReportValidationError, validate_report_payload
from codex_ledger.reports.workspaces import build_workspace_report
from codex_ledger.storage.migrations import connect_database, default_database_path


def verify_ledger(archive_home: Path) -> dict[str, Any]:
    database_path = default_database_path(archive_home)
    with connect_database(database_path) as connection:
        checks = [
            _check_priced_rows_have_amount(connection),
            _check_unpriced_rows_have_null_amount(connection),
            _check_cost_rows_only_price_token_events(connection),
            _check_pricing_rows_reference_existing_events(connection),
        ]
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
    }


def verify_reports(
    archive_home: Path,
    *,
    rule_set_id: str | None = None,
) -> dict[str, Any]:
    max_date = _max_event_date(archive_home)
    aggregate = build_aggregate_report(
        archive_home=archive_home,
        period="year",
        as_of=max_date,
        rule_set_id=rule_set_id,
    )
    workspace = build_workspace_report(
        archive_home=archive_home,
        period="year",
        as_of=max_date,
        rule_set_id=rule_set_id,
    )
    agents = build_agent_report(
        archive_home=archive_home,
        period="year",
        as_of=max_date,
        rule_set_id=rule_set_id,
    )

    aggregate_totals = aggregate["data"]["selected_period_totals"]
    workspace_token_total = sum(
        int(item["total_tokens"]) for item in workspace["data"]["workspaces"]
    )
    agent_token_total = int(agents["summary"]["root_usage"]["total_tokens"]) + int(
        agents["summary"]["subagent_usage"]["total_tokens"]
    )

    checks = [
        _schema_check("aggregate_report_schema_valid", aggregate),
        _schema_check("workspace_report_schema_valid", workspace),
        _schema_check("agent_report_schema_valid", agents),
        {
            "name": "workspace_totals_match_aggregate",
            "ok": workspace_token_total == int(aggregate_totals["total_tokens"]),
            "detail": {
                "workspace_total_tokens": workspace_token_total,
                "aggregate_total_tokens": int(aggregate_totals["total_tokens"]),
            },
        },
        {
            "name": "agent_totals_match_aggregate",
            "ok": agent_token_total == int(aggregate_totals["total_tokens"]),
            "detail": {
                "agent_total_tokens": agent_token_total,
                "aggregate_total_tokens": int(aggregate_totals["total_tokens"]),
            },
        },
    ]

    if aggregate["pricing"]["included"]:
        priced_tokens = int(aggregate["pricing"]["priced_token_total"])
        unpriced_tokens = int(aggregate["pricing"]["unpriced_token_total"])
        workspace_priced_tokens = sum(
            int(item["priced_token_total"]) for item in workspace["data"]["workspaces"]
        )
        workspace_unpriced_tokens = sum(
            int(item["unpriced_token_total"]) for item in workspace["data"]["workspaces"]
        )
        checks.extend(
            [
                {
                    "name": "workspace_priced_tokens_match_aggregate",
                    "ok": workspace_priced_tokens == priced_tokens,
                    "detail": {
                        "workspace_priced_tokens": workspace_priced_tokens,
                        "aggregate_priced_tokens": priced_tokens,
                    },
                },
                {
                    "name": "workspace_unpriced_tokens_match_aggregate",
                    "ok": workspace_unpriced_tokens == unpriced_tokens,
                    "detail": {
                        "workspace_unpriced_tokens": workspace_unpriced_tokens,
                        "aggregate_unpriced_tokens": unpriced_tokens,
                    },
                },
                {
                    "name": "priced_rows_have_amount_for_reporting",
                    "ok": _priced_rows_have_amount(archive_home),
                    "detail": {},
                },
            ]
        )

    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "pricing": aggregate["pricing"],
    }


def format_verify_table(payload: dict[str, Any], *, label: str) -> str:
    status = "ok" if payload["ok"] else "failed"
    lines = [f"Verify {label}: {status}"]
    for check in payload["checks"]:
        line = f"- {check['name']}: {'ok' if check['ok'] else 'failed'}"
        if not check["ok"] and check["detail"]:
            line += f" ({check['detail']})"
        lines.append(line)
    return "\n".join(lines)


def _max_event_date(archive_home: Path) -> date:
    database_path = default_database_path(archive_home)
    with connect_database(database_path) as connection:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(substr(event_ts_utc, 1, 10)), '1970-01-01')
            FROM usage_events
            """
        ).fetchone()
    return date.fromisoformat(str(row[0]))


def _priced_rows_have_amount(archive_home: Path) -> bool:
    database_path = default_database_path(archive_home)
    with connect_database(database_path) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM cost_estimates
            WHERE estimate_status = 'priced'
              AND amount IS NULL
            """
        ).fetchone()
    return int(row[0]) == 0


def _schema_check(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        validate_report_payload(payload)
    except ReportValidationError as exc:
        return {
            "name": name,
            "ok": False,
            "detail": {"error": str(exc)},
        }
    return {
        "name": name,
        "ok": True,
        "detail": {},
    }


def _check_priced_rows_have_amount(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM cost_estimates
        WHERE estimate_status = 'priced'
          AND amount IS NULL
        """
    ).fetchone()
    count = int(row[0])
    return {
        "name": "priced_rows_have_amount",
        "ok": count == 0,
        "detail": {"mismatch_count": count},
    }


def _check_unpriced_rows_have_null_amount(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM cost_estimates
        WHERE estimate_status != 'priced'
          AND amount IS NOT NULL
        """
    ).fetchone()
    count = int(row[0])
    return {
        "name": "unpriced_rows_have_null_amount",
        "ok": count == 0,
        "detail": {"mismatch_count": count},
    }


def _check_cost_rows_only_price_token_events(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM cost_estimates AS ce
        JOIN usage_events AS ue
          ON ue.event_id = ce.event_id
        WHERE ue.total_tokens IS NULL
          AND ue.input_tokens IS NULL
          AND ue.cached_input_tokens IS NULL
          AND ue.output_tokens IS NULL
        """
    ).fetchone()
    count = int(row[0])
    return {
        "name": "cost_rows_only_price_token_events",
        "ok": count == 0,
        "detail": {"mismatch_count": count},
    }


def _check_pricing_rows_reference_existing_events(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM cost_estimates AS ce
        LEFT JOIN usage_events AS ue
          ON ue.event_id = ce.event_id
        WHERE ue.event_id IS NULL
        """
    ).fetchone()
    count = int(row[0])
    return {
        "name": "cost_rows_reference_existing_events",
        "ok": count == 0,
        "detail": {"mismatch_count": count},
    }
