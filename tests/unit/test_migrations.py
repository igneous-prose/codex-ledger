from __future__ import annotations

import sqlite3
from pathlib import Path

from codex_ledger.storage.migrations import apply_migrations


def test_apply_migrations_creates_schema_tracking_table(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"

    applied = apply_migrations(database_path)

    assert applied == [
        "0001_initial.sql",
        "0002_phase1_ledger.sql",
        "0003_phase2_workspace_lineage.sql",
        "0004_phase2.1_agent_observability.sql",
        "0005_phase3_pricing.sql",
    ]

    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    finally:
        connection.close()

    assert rows == [
        ("0001", "0001_initial.sql"),
        ("0002", "0002_phase1_ledger.sql"),
        ("0003", "0003_phase2_workspace_lineage.sql"),
        ("0004", "0004_phase2.1_agent_observability.sql"),
        ("0005", "0005_phase3_pricing.sql"),
    ]
    assert {
        "agent_runs",
        "cost_estimates",
        "import_batches",
        "models",
        "pricing_rule_sets",
        "provider_sessions",
        "raw_files",
        "schema_migrations",
        "usage_events",
        "workspace_aliases",
        "workspaces",
    }.issubset({str(row[0]) for row in tables})


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"

    apply_migrations(database_path)
    applied_again = apply_migrations(database_path)

    assert applied_again == []
