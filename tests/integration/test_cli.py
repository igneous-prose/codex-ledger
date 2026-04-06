from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_module_help_is_clean() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "codex_ledger", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "codex-ledger" in result.stdout
    assert "doctor" in result.stdout
    assert "migrate" in result.stdout


def test_migrate_command_creates_database(tmp_path: Path) -> None:
    database_path = tmp_path / "custom.sqlite3"

    result = subprocess.run(
        [sys.executable, "-m", "codex_ledger", "migrate", "--database", str(database_path)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert database_path.exists()
    assert "0001_initial.sql" in result.stdout
