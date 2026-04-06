from __future__ import annotations

import sqlite3
from pathlib import Path

from codex_ledger.storage.migrations import apply_migrations


def test_apply_migrations_creates_schema_tracking_table(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"

    applied = apply_migrations(database_path)

    assert applied == ["0001_initial.sql"]

    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        connection.close()

    assert rows == [("0001", "0001_initial.sql")]


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"

    apply_migrations(database_path)
    applied_again = apply_migrations(database_path)

    assert applied_again == []
