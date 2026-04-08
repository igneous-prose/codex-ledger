from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path


def default_database_path(archive_home: Path) -> Path:
    return archive_home / "ledger" / "codex-ledger.sqlite3"


def connect_database(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def applied_versions(connection: sqlite3.Connection) -> set[str]:
    if not has_schema_migrations_table(connection):
        return set()
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {str(row[0]) for row in rows}


def has_schema_migrations_table(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_migrations'
        """
    ).fetchone()
    return row is not None


def migration_files() -> list[tuple[str, Traversable]]:
    migration_root = resources.files("codex_ledger.migrations")
    files = [
        item
        for item in migration_root.iterdir()
        if item.name.endswith(".sql") and item.name[:4].isdigit()
    ]
    return sorted((item.name, item) for item in files)


def migration_catalog() -> list[tuple[str, str, Traversable]]:
    return [
        (filename.split("_", maxsplit=1)[0], filename, file_ref)
        for filename, file_ref in migration_files()
    ]


def migration_filenames() -> list[str]:
    return [filename for _, filename, _ in migration_catalog()]


def migration_filename_by_version() -> dict[str, str]:
    return {version: filename for version, filename, _ in migration_catalog()}


def apply_migrations(database_path: Path) -> list[str]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []

    with connect_database(database_path) as connection:
        existing = applied_versions(connection)
        for version, filename, file_ref in migration_catalog():
            if version in existing:
                continue

            sql = file_ref.read_text(encoding="utf-8")
            connection.executescript(sql)
            connection.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at_utc)
                VALUES (?, ?, ?)
                """,
                (version, filename, datetime.now(UTC).isoformat()),
            )
            applied.append(filename)
            existing.add(version)

    return applied
