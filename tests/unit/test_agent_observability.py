from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from codex_ledger.domain.records import ImportCandidate
from codex_ledger.ingest.service import run_import_batch
from codex_ledger.reports.agents import build_agent_report, explain_agent_run
from tests.test_support import fetch_all, fixture_path, open_database


def test_root_vs_subagent_usage_is_reported_separately(tmp_path: Path) -> None:
    archive_home = _import_lineage_snapshot(tmp_path)

    payload = build_agent_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 5),
    )

    assert payload["summary"]["root_usage"] == {
        "event_count": 4,
        "total_tokens": 16,
        "input_tokens": 12,
        "cached_input_tokens": 1,
        "output_tokens": 4,
        "reasoning_output_tokens": 1,
    }
    assert payload["summary"]["subagent_usage"] == {
        "event_count": 3,
        "total_tokens": 11,
        "input_tokens": 8,
        "cached_input_tokens": 0,
        "output_tokens": 3,
        "reasoning_output_tokens": 1,
    }


def test_requested_model_and_observed_model_are_preserved(tmp_path: Path) -> None:
    archive_home = _import_lineage_snapshot(tmp_path)
    connection = open_database(archive_home)
    try:
        row = connection.execute(
            """
            SELECT requested_model_id, model_id, lineage_status
            FROM agent_runs
            WHERE lineage_key = 'session'
              AND agent_kind = 'subagent'
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == ("gpt-5.4-mini", "gpt-5.4", "resolved")


def test_unresolved_spawn_intent_stays_unresolved_until_child_arrives(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    run_import_batch(
        archive_home=archive_home,
        candidates=(
            ImportCandidate(
                fixture_path("lineage_parent_rollout.jsonl"),
                "local_rollout_file",
            ),
        ),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    connection = open_database(archive_home)
    try:
        row = connection.execute(
            """
            SELECT lineage_key, lineage_status, lineage_confidence, unresolved_reason
            FROM agent_runs
            WHERE lineage_key = 'spawn:child-session'
            """
        ).fetchone()
    finally:
        connection.close()

    payload = build_agent_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 5),
    )
    assert row == (
        "spawn:child-session",
        "spawn_only_unmatched",
        "spawn_event_only",
        "child_session_missing",
    )
    assert payload["summary"]["unresolved_spawn_count"] == 1


def test_orphan_child_session_is_not_force_matched(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    run_import_batch(
        archive_home=archive_home,
        candidates=(
            ImportCandidate(
                fixture_path("lineage_child_rollout.jsonl"),
                "local_rollout_file",
            ),
        ),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    connection = open_database(archive_home)
    try:
        row = connection.execute(
            """
            SELECT parent_agent_run_key, lineage_status, unresolved_reason
            FROM agent_runs
            WHERE lineage_key = 'session'
            """
        ).fetchone()
    finally:
        connection.close()

    payload = build_agent_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 5),
    )
    assert row == (None, "child_only_orphaned", "parent_session_missing")
    assert payload["summary"]["orphan_child_count"] == 1


def test_agent_diagnostics_are_deterministic_for_same_snapshot(tmp_path: Path) -> None:
    archive_home = _import_lineage_snapshot(tmp_path)

    first = build_agent_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 5),
    )
    second = build_agent_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 5),
    )

    assert first == second


def test_explain_agent_defaults_to_redacted_workspace_labels(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    workspace_root = tmp_path / "absolute-workspace"
    nested = workspace_root / "app"
    nested.mkdir(parents=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    rollout = tmp_path / "absolute-rollout.jsonl"
    _write_rollout(
        rollout,
        [
            {
                "timestamp": "2026-04-11T09:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "absolute-session",
                    "timestamp": "2026-04-11T08:59:55Z",
                    "cwd": str(workspace_root),
                    "originator": "Desktop",
                    "cli_version": "0.120.0",
                    "source": "desktop",
                },
            },
            {
                "timestamp": "2026-04-11T09:00:01Z",
                "type": "turn_context",
                "payload": {
                    "turn_id": "absolute-turn",
                    "cwd": str(nested),
                    "model": "gpt-5.4",
                },
            },
            {
                "timestamp": "2026-04-11T09:00:02Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 5,
                            "cached_input_tokens": 0,
                            "output_tokens": 2,
                            "reasoning_output_tokens": 1,
                            "total_tokens": 7,
                        }
                    },
                },
            },
        ],
    )
    run_import_batch(
        archive_home=archive_home,
        candidates=(ImportCandidate(rollout, "local_rollout_file"),),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    connection = open_database(archive_home)
    try:
        agent_run_key = str(
            connection.execute(
                "SELECT agent_run_key FROM agent_runs WHERE lineage_key = 'root'"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    report_payload = build_agent_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 11),
    )
    explain_payload = explain_agent_run(
        archive_home=archive_home,
        agent_run_key=agent_run_key,
    )

    serialized = json.dumps({"report": report_payload, "explain": explain_payload}, sort_keys=True)
    assert str(workspace_root) not in serialized
    assert str(nested) not in serialized


def test_rebuild_from_raw_preserves_agent_observability_payload(tmp_path: Path) -> None:
    archive_one = _import_lineage_snapshot(tmp_path / "run-one")
    report_one = build_agent_report(
        archive_home=archive_one,
        period="day",
        as_of=date(2026, 4, 5),
    )

    connection = open_database(archive_one)
    try:
        stored_relpaths = [
            str(row[0])
            for row in connection.execute(
                "SELECT stored_relpath FROM raw_files ORDER BY stored_relpath"
            ).fetchall()
        ]
        rows_one = fetch_all(
            connection,
            """
            SELECT lineage_key, agent_kind, requested_model_id, model_id,
                   lineage_status, lineage_confidence, unresolved_reason
            FROM agent_runs
            ORDER BY session_key, lineage_key
            """,
        )
    finally:
        connection.close()

    archive_two = tmp_path / "run-two"
    candidates = tuple(
        ImportCandidate(archive_one / "raw" / relpath, "local_rollout_file")
        for relpath in stored_relpaths
    )
    run_import_batch(
        archive_home=archive_two,
        candidates=candidates,
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    connection = open_database(archive_two)
    try:
        rows_two = fetch_all(
            connection,
            """
            SELECT lineage_key, agent_kind, requested_model_id, model_id,
                   lineage_status, lineage_confidence, unresolved_reason
            FROM agent_runs
            ORDER BY session_key, lineage_key
            """,
        )
    finally:
        connection.close()

    report_two = build_agent_report(
        archive_home=archive_two,
        period="day",
        as_of=date(2026, 4, 5),
    )
    assert rows_one == rows_two
    assert report_one == report_two


def _import_lineage_snapshot(tmp_path: Path) -> Path:
    archive_home = tmp_path / "archive"
    run_import_batch(
        archive_home=archive_home,
        candidates=(
            ImportCandidate(fixture_path("lineage_parent_rollout.jsonl"), "local_rollout_file"),
            ImportCandidate(fixture_path("lineage_child_rollout.jsonl"), "local_rollout_file"),
        ),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )
    return archive_home


def _write_rollout(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )
