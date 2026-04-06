from __future__ import annotations

from pathlib import Path

from codex_ledger.domain.records import ImportCandidate
from codex_ledger.ingest.service import run_import_batch
from codex_ledger.normalize.workspaces import resolve_workspace
from codex_ledger.storage.archive import archive_raw_file
from codex_ledger.utils.hashing import sha256_file, sha256_text
from tests.test_support import fetch_all, fixture_path, open_database


def test_import_same_raw_file_twice_does_not_duplicate_rows(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    candidates = (ImportCandidate(fixture_path("sample_rollout.jsonl"), "local_rollout_file"),)

    summary_one, _ = run_import_batch(
        archive_home=archive_home,
        candidates=candidates,
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )
    summary_two, _ = run_import_batch(
        archive_home=archive_home,
        candidates=candidates,
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    assert summary_one.imported_file_count == 1
    assert summary_two.skipped_file_count == 1

    connection = open_database(archive_home)
    try:
        counts = {
            "raw_files": connection.execute("SELECT COUNT(*) FROM raw_files").fetchone()[0],
            "sessions": connection.execute("SELECT COUNT(*) FROM provider_sessions").fetchone()[0],
            "agent_runs": connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0],
            "events": connection.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0],
        }
    finally:
        connection.close()

    assert counts == {
        "raw_files": 1,
        "sessions": 1,
        "agent_runs": 1,
        "events": 5,
    }


def test_malformed_jsonl_does_not_abort_batch(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    candidates = (
        ImportCandidate(fixture_path("sample_rollout.jsonl"), "local_rollout_file"),
        ImportCandidate(fixture_path("malformed_rollout.jsonl"), "local_rollout_file"),
    )

    summary, outcomes = run_import_batch(
        archive_home=archive_home,
        candidates=candidates,
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    assert summary.imported_file_count == 1
    assert summary.failed_file_count == 1
    assert {outcome.status for outcome in outcomes} == {"imported", "malformed_jsonl"}

    connection = open_database(archive_home)
    try:
        raw_rows = fetch_all(
            connection,
            "SELECT parse_status, event_count FROM raw_files ORDER BY original_path",
        )
        event_count = connection.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    finally:
        connection.close()

    assert raw_rows == [("malformed_jsonl", 0), ("parsed", 5)]
    assert event_count == 5


def test_rebuild_from_archived_raw_yields_equivalent_canonical_rows(tmp_path: Path) -> None:
    archive_one = tmp_path / "archive-one"
    candidates = (ImportCandidate(fixture_path("sample_rollout.jsonl"), "local_rollout_file"),)
    run_import_batch(
        archive_home=archive_one,
        candidates=candidates,
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    connection_one = open_database(archive_one)
    try:
        stored_relpath = str(
            connection_one.execute("SELECT stored_relpath FROM raw_files").fetchone()[0]
        )
        rows_one = fetch_all(
            connection_one,
            """
            SELECT event_type, payload_type, turn_index, raw_cwd, session_cwd, model_id,
                   input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens,
                   total_tokens, workspace_strategy
            FROM usage_events
            ORDER BY event_index
            """,
        )
    finally:
        connection_one.close()

    archive_two = tmp_path / "archive-two"
    archived_raw_path = archive_one / "raw" / stored_relpath
    run_import_batch(
        archive_home=archive_two,
        candidates=(ImportCandidate(archived_raw_path, "local_rollout_file"),),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    connection_two = open_database(archive_two)
    try:
        rows_two = fetch_all(
            connection_two,
            """
            SELECT event_type, payload_type, turn_index, raw_cwd, session_cwd, model_id,
                   input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens,
                   total_tokens, workspace_strategy
            FROM usage_events
            ORDER BY event_index
            """,
        )
    finally:
        connection_two.close()

    assert rows_one == rows_two


def test_turn_context_cwd_overrides_session_cwd(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    run_import_batch(
        archive_home=archive_home,
        candidates=(ImportCandidate(fixture_path("sample_rollout.jsonl"), "local_rollout_file"),),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    connection = open_database(archive_home)
    try:
        row = connection.execute(
            """
            SELECT raw_cwd, session_cwd, workspace_strategy
            FROM usage_events
            WHERE payload_type = 'token_count'
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == (
        "workspace-alpha/project/subdir",
        "workspace-alpha/project",
        "turn_context.cwd",
    )


def test_unknown_workspace_fallback_works(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    run_import_batch(
        archive_home=archive_home,
        candidates=(
            ImportCandidate(fixture_path("unknown_workspace_rollout.jsonl"), "local_rollout_file"),
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
            SELECT workspace_key, raw_cwd, session_cwd, workspace_strategy
            FROM usage_events
            WHERE payload_type = 'token_count'
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == ("workspace-unknown", None, None, "unknown")


def test_raw_file_hashes_are_stable(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    fixture = fixture_path("sample_rollout.jsonl")

    first_hash, stored_relpath, _ = archive_raw_file(
        raw_root, fixture, "codex", "local_rollout_file"
    )
    second_hash, second_relpath, _ = archive_raw_file(
        raw_root, fixture, "codex", "local_rollout_file"
    )

    assert first_hash == sha256_file(fixture)
    assert second_hash == first_hash
    assert second_relpath == stored_relpath


def test_path_like_model_ids_remain_models_not_workspaces(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    candidates = (
        ImportCandidate(
            fixture_path("path_like_model_report.json"),
            "imported_json_report",
        ),
    )
    run_import_batch(
        archive_home=archive_home,
        candidates=candidates,
        provider="codex",
        host="imported_json",
        source_kind="imported_json_report",
        full_backfill=False,
    )

    connection = open_database(archive_home)
    try:
        model_row = connection.execute(
            "SELECT model_id FROM models WHERE model_id = 'provider/model-like/path'"
        ).fetchone()
        workspace_rows = fetch_all(
            connection,
            "SELECT workspace_key, resolved_root_path FROM workspaces ORDER BY workspace_key",
        )
    finally:
        connection.close()

    expected_rows = sorted(
        [
            (
                f"workspace-{sha256_text('workspace-model/project')[:16]}",
                "workspace-model/project",
            ),
            (
                f"workspace-{sha256_text('workspace-model/project/task')[:16]}",
                "workspace-model/project/task",
            ),
        ]
    )
    assert model_row == ("provider/model-like/path",)
    assert workspace_rows == expected_rows


def test_workspace_privacy_defaults_use_redacted_labels(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    run_import_batch(
        archive_home=archive_home,
        candidates=(ImportCandidate(fixture_path("sample_rollout.jsonl"), "local_rollout_file"),),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    connection = open_database(archive_home)
    try:
        row = connection.execute(
            """
            SELECT workspace_key, resolved_root_path, display_label, redacted_display_label,
                   resolution_strategy
            FROM workspaces
            """
        ).fetchone()
    finally:
        connection.close()

    expected_hash = sha256_text("workspace-alpha/project/subdir")
    assert row == (
        f"workspace-{expected_hash[:16]}",
        "workspace-alpha/project/subdir",
        "subdir",
        f"workspace-{expected_hash[:8]}",
        "turn_context.cwd",
    )
    assert row[3] != row[1]
    assert row[3] != row[2]


def test_workspace_record_exposes_redacted_label_alias() -> None:
    workspace = resolve_workspace("workspace-alpha/project/subdir", None)

    assert workspace.redacted_label == workspace.redacted_display_label
    assert workspace.display_label == "subdir"
    assert workspace.redacted_label != workspace.resolved_root_path


def test_fixture_files_are_sanitized() -> None:
    fixture_dir = fixture_path("sample_rollout.jsonl").parent
    forbidden_fragments = ("/Users/", "\\Users\\", "markhardy")
    for path in sorted(fixture_dir.iterdir()):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        assert all(fragment not in content for fragment in forbidden_fragments), path.name
