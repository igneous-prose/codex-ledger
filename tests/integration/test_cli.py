from __future__ import annotations

import json
import os
import sqlite3
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
    assert "price" in result.stdout
    assert "report" in result.stdout
    assert "explain" in result.stdout
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
    assert "0003_phase2_workspace_lineage.sql" in result.stdout
    assert "0004_phase21_agent_observability.sql" in result.stdout
    assert "0005_phase3_pricing.sql" in result.stdout


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
        "0003_phase2_workspace_lineage.sql",
        "0004_phase21_agent_observability.sql",
        "0005_phase3_pricing.sql",
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


def test_report_agents_command_emits_json_diagnostics(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    fixture_dir = Path(__file__).resolve().parents[1] / "fixtures" / "codex"
    env = {**os.environ, "PYTHONPATH": "src"}
    session_dir = tmp_path / ".codex" / "sessions" / "2026" / "04" / "05"
    session_dir.mkdir(parents=True)
    for name in ("lineage_parent_rollout.jsonl", "lineage_child_rollout.jsonl"):
        target = session_dir / name
        target.write_text((fixture_dir / name).read_text(encoding="utf-8"), encoding="utf-8")

    import_result = subprocess.run(
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
        env={**env, "HOME": str(tmp_path)},
    )
    assert import_result.returncode == 0

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codex_ledger",
            "report",
            "agents",
            "--period",
            "day",
            "--as-of",
            "2026-04-05",
            "--format",
            "json",
            "--archive-home",
            str(archive_home),
        ],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "phase2.1-agent-diagnostics-v1"
    assert payload["summary"]["matched_child_count"] == 1
    assert payload["summary"]["root_usage"]["total_tokens"] == 16
    assert payload["summary"]["subagent_usage"]["total_tokens"] == 11


def test_explain_agent_command_defaults_to_redacted_workspace_output(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    workspace_root = tmp_path / "private-workspace"
    nested = workspace_root / "nested"
    nested.mkdir(parents=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    rollout = tmp_path / "absolute-rollout.jsonl"
    rollout.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-12T09:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": "private-session",
                            "timestamp": "2026-04-12T08:59:55Z",
                            "cwd": str(workspace_root),
                            "originator": "Desktop",
                            "cli_version": "0.120.0",
                            "source": "desktop",
                        },
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-12T09:00:01Z",
                        "type": "turn_context",
                        "payload": {
                            "turn_id": "private-turn",
                            "cwd": str(nested),
                            "model": "gpt-5.4",
                        },
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-12T09:00:02Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": 3,
                                    "cached_input_tokens": 0,
                                    "output_tokens": 1,
                                    "reasoning_output_tokens": 1,
                                    "total_tokens": 4,
                                }
                            },
                        },
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": "src"}

    session_dir = tmp_path / ".codex" / "sessions" / "2026" / "04" / "12"
    session_dir.mkdir(parents=True)
    target = session_dir / rollout.name
    target.write_text(rollout.read_text(encoding="utf-8"), encoding="utf-8")
    import_result = subprocess.run(
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
        env={**env, "HOME": str(tmp_path)},
    )
    assert import_result.returncode == 0

    connection = sqlite3.connect(archive_home / "ledger" / "codex-ledger.sqlite3")
    try:
        agent_run_key = str(
            connection.execute(
                "SELECT agent_run_key FROM agent_runs WHERE lineage_key = 'root'"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codex_ledger",
            "explain",
            "agent",
            "--agent-run",
            agent_run_key,
            "--format",
            "json",
            "--archive-home",
            str(archive_home),
        ],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    serialized = json.dumps(payload, sort_keys=True)
    assert str(workspace_root) not in serialized
    assert str(nested) not in serialized


def test_price_coverage_command_emits_json_diagnostics(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "sample_rollout.jsonl"
    env = {**os.environ, "PYTHONPATH": "src"}
    session_dir = tmp_path / ".codex" / "sessions" / "2026" / "04" / "20"
    session_dir.mkdir(parents=True)
    target = session_dir / fixture.name
    target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    sync_result = subprocess.run(
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
        env={**env, "HOME": str(tmp_path)},
    )
    assert sync_result.returncode == 0

    price_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codex_ledger",
            "price",
            "recalc",
            "--rule-set",
            "reference_usd_openai_standard_2026_04_07",
            "--archive-home",
            str(archive_home),
        ],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )
    assert price_result.returncode == 0

    coverage_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codex_ledger",
            "price",
            "coverage",
            "--rule-set",
            "reference_usd_openai_standard_2026_04_07",
            "--format",
            "json",
            "--archive-home",
            str(archive_home),
        ],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )

    assert coverage_result.returncode == 0
    payload = json.loads(coverage_result.stdout)
    assert payload["schema_version"] == "phase3-pricing-coverage-v1"
    assert payload["summary"]["priced_event_count"] == 1
    assert payload["summary"]["unpriced_event_count"] == 0


def test_report_aggregate_command_emits_json(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "sample_rollout.jsonl"
    env = {**os.environ, "PYTHONPATH": "src"}
    session_dir = tmp_path / ".codex" / "sessions" / "2026" / "04" / "22"
    session_dir.mkdir(parents=True)
    target = session_dir / fixture.name
    target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    sync_result = subprocess.run(
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
        env={**env, "HOME": str(tmp_path)},
    )
    assert sync_result.returncode == 0

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codex_ledger",
            "report",
            "aggregate",
            "--period",
            "month",
            "--as-of",
            "2026-04-30",
            "--format",
            "json",
            "--archive-home",
            str(archive_home),
        ],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "phase4-aggregate-report-v1"
    assert payload["pricing"]["selected_rule_set_id"] == "reference_usd_openai_standard_2026_04_07"


def test_explain_day_command_emits_json(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "sample_rollout.jsonl"
    env = {**os.environ, "PYTHONPATH": "src"}
    session_dir = tmp_path / ".codex" / "sessions" / "2026" / "04" / "01"
    session_dir.mkdir(parents=True)
    target = session_dir / fixture.name
    target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    sync_result = subprocess.run(
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
        env={**env, "HOME": str(tmp_path)},
    )
    assert sync_result.returncode == 0

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "codex_ledger",
            "explain",
            "day",
            "--date",
            "2026-04-01",
            "--format",
            "json",
            "--archive-home",
            str(archive_home),
        ],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "phase4-explain-report-v1"
    assert payload["source_artifacts"]
