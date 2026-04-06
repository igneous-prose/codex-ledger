from __future__ import annotations

import json
import os
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
    assert "sync" in result.stdout
    assert "import" in result.stdout
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
    assert "0002_phase1_ledger.sql" in result.stdout


def test_import_codex_json_command_imports_fixture(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "imported_report.json"
    env = {**os.environ, "PYTHONPATH": "src"}

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codex_ledger",
            "import",
            "codex-json",
            "--input",
            str(fixture),
            "--archive-home",
            str(archive_home),
        ],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert "imported:" in result.stdout


def test_sync_command_imports_local_rollouts_from_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    session_dir = home / ".codex" / "sessions" / "2026" / "04" / "01"
    session_dir.mkdir(parents=True)
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "sample_rollout.jsonl"
    target = session_dir / fixture.name
    target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    archive_home = tmp_path / "archive"
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": "src"}

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codex_ledger",
            "sync",
            "--archive-home",
            str(archive_home),
        ],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert "Imported files: 1" in result.stdout


def test_doctor_reports_persistence_source_dirs_database_and_migrations(tmp_path: Path) -> None:
    home = tmp_path / "home"
    archive_home = tmp_path / "archive"
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": "src"}

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codex_ledger",
            "doctor",
            "--archive-home",
            str(archive_home),
            "--json",
        ],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["history_persistence_status"] == "disabled"
    assert payload["database_path"].endswith("ledger/codex-ledger.sqlite3")
    assert payload["migration_status"]["pending"] == [
        "0001_initial.sql",
        "0002_phase1_ledger.sql",
    ]
    assert payload["source_roots"] == [
        {
            "exists": False,
            "jsonl_count": 0,
            "path": str(home / ".codex" / "sessions"),
        },
        {
            "exists": False,
            "jsonl_count": 0,
            "path": str(home / ".codex" / "archived_sessions"),
        },
    ]
