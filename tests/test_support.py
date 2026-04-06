from __future__ import annotations

import sqlite3
from pathlib import Path

from codex_ledger.storage.migrations import default_database_path


def fixture_path(name: str) -> Path:
    return Path(__file__).parent / "fixtures" / "codex" / name


def open_database(archive_home: Path) -> sqlite3.Connection:
    return sqlite3.connect(default_database_path(archive_home))


def fetch_all(connection: sqlite3.Connection, sql: str) -> list[tuple[object, ...]]:
    return [tuple(row) for row in connection.execute(sql).fetchall()]
