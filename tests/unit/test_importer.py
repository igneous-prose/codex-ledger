from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_ledger.domain.records import ImportCandidate, WorkspaceRecord
from codex_ledger.ingest.service import run_import_batch
from codex_ledger.normalize.privacy import render_workspace_label
from codex_ledger.normalize.workspaces import resolve_workspace
from codex_ledger.storage.archive import archive_raw_file
from codex_ledger.storage.repository import fetch_workspace_alias_map, upsert_workspace_alias
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
        "raw_cwd",
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


def test_archive_raw_file_rejects_symlink_target(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    fixture = fixture_path("sample_rollout.jsonl")
    content_hash = sha256_file(fixture)
    stored_relpath = Path(f"codex/local_rollout_file/{content_hash[:2]}/{content_hash}.jsonl")
    target_path = raw_root / stored_relpath
    victim_path = tmp_path / "victim.txt"
    victim_path.write_text("do not overwrite\n", encoding="utf-8")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.symlink_to(victim_path)

    with pytest.raises(ValueError, match="symlink"):
        archive_raw_file(raw_root, fixture, "codex", "local_rollout_file")

    assert victim_path.read_text(encoding="utf-8") == "do not overwrite\n"


def test_archive_raw_file_rejects_symlinked_archive_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real-raw"
    real_root.mkdir()
    symlink_root = tmp_path / "raw-link"
    symlink_root.symlink_to(real_root, target_is_directory=True)
    fixture = fixture_path("sample_rollout.jsonl")

    with pytest.raises(ValueError, match="symlinked archive root"):
        archive_raw_file(symlink_root, fixture, "codex", "local_rollout_file")


def test_import_rejects_oversized_local_rollout_file(tmp_path: Path, monkeypatch) -> None:
    archive_home = tmp_path / "archive"
    oversized = tmp_path / "oversized.jsonl"
    oversized.write_text('{"type":"session_meta","payload":{"id":"s"}}\n', encoding="utf-8")
    monkeypatch.setattr("codex_ledger.providers.codex.parser.MAX_IMPORT_FILE_BYTES", 8)
    monkeypatch.setattr("codex_ledger.storage.archive.MAX_ARCHIVE_COPY_BYTES", 8)

    summary, outcomes = run_import_batch(
        archive_home=archive_home,
        candidates=(ImportCandidate(oversized, "local_rollout_file"),),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    assert summary.failed_file_count == 1
    assert outcomes[0].status == "file_too_large"
    assert "exceeds configured limit" in str(outcomes[0].detail)


def test_archive_raw_file_rejects_oversized_input_before_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_root = tmp_path / "raw"
    oversized = tmp_path / "oversized.jsonl"
    oversized.write_text("x" * 16, encoding="utf-8")
    monkeypatch.setattr("codex_ledger.storage.archive.MAX_ARCHIVE_COPY_BYTES", 8)

    def fail_if_hashed(_: Path) -> str:
        raise AssertionError("sha256_file should not be called for oversized inputs")

    monkeypatch.setattr("codex_ledger.storage.archive.sha256_file", fail_if_hashed)

    with pytest.raises(ValueError, match="exceeds configured limit"):
        archive_raw_file(raw_root, oversized, "codex", "local_rollout_file")


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
        "raw_cwd",
    )
    assert row[3] != row[1]
    assert row[3] != row[2]


def test_workspace_record_exposes_redacted_label_alias() -> None:
    workspace = resolve_workspace("workspace-alpha/project/subdir", None)

    assert workspace.redacted_label == workspace.redacted_display_label
    assert workspace.display_label == "subdir"
    assert workspace.redacted_label != workspace.resolved_root_path


def test_project_root_resolution_uses_marker(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace-root"
    nested = workspace_root / "src" / "nested"
    nested.mkdir(parents=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
    rollout = tmp_path / "project-root-rollout.jsonl"
    _write_rollout(
        rollout,
        [
            {
                "timestamp": "2026-04-10T09:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "project-root-session",
                    "timestamp": "2026-04-10T08:59:55Z",
                    "cwd": str(workspace_root),
                    "originator": "Desktop",
                    "cli_version": "0.120.0",
                    "source": "desktop",
                },
            },
            {
                "timestamp": "2026-04-10T09:00:01Z",
                "type": "turn_context",
                "payload": {
                    "turn_id": "turn-project-root",
                    "cwd": str(nested),
                    "model": "gpt-5.4",
                },
            },
        ],
    )

    archive_home = tmp_path / "archive"
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
        row = connection.execute(
            """
            SELECT raw_cwd, resolved_root_path, resolution_strategy, display_label
            FROM workspaces
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == (
        str(nested),
        str(workspace_root),
        "project_root_marker",
        "workspace-root",
    )


def test_git_root_fallback_uses_repository_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "git-workspace"
    nested = workspace_root / "app" / "module"
    nested.mkdir(parents=True)
    (workspace_root / ".git").mkdir()
    rollout = tmp_path / "git-root-rollout.jsonl"
    _write_rollout(
        rollout,
        [
            {
                "timestamp": "2026-04-10T10:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "git-root-session",
                    "timestamp": "2026-04-10T09:59:55Z",
                    "cwd": str(workspace_root),
                    "originator": "Desktop",
                    "cli_version": "0.120.0",
                    "source": "desktop",
                },
            },
            {
                "timestamp": "2026-04-10T10:00:01Z",
                "type": "turn_context",
                "payload": {
                    "turn_id": "turn-git-root",
                    "cwd": str(nested),
                    "model": "gpt-5.4",
                },
            },
        ],
    )

    archive_home = tmp_path / "archive"
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
        row = connection.execute(
            """
            SELECT raw_cwd, resolved_root_path, resolution_strategy
            FROM workspaces
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == (str(nested), str(workspace_root), "git_root")


def test_alias_mode_uses_persisted_workspace_alias(tmp_path: Path) -> None:
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
        workspace = _fetch_single_workspace(connection)
        upsert_workspace_alias(
            connection,
            workspace_key=workspace.workspace_key,
            alias_label="client-workspace",
        )
        alias_map = fetch_workspace_alias_map(connection)
    finally:
        connection.close()

    assert render_workspace_label(workspace, mode="alias", aliases=alias_map) == "client-workspace"


def test_full_mode_returns_full_workspace_path() -> None:
    workspace = resolve_workspace("workspace-alpha/project/subdir", None)

    assert render_workspace_label(workspace, mode="full") == "workspace-alpha/project/subdir"


def test_parent_child_lineage_is_populated_when_source_evidence_exists(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    candidates = (
        ImportCandidate(fixture_path("lineage_parent_rollout.jsonl"), "local_rollout_file"),
        ImportCandidate(fixture_path("lineage_child_rollout.jsonl"), "local_rollout_file"),
    )
    run_import_batch(
        archive_home=archive_home,
        candidates=candidates,
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )

    connection = open_database(archive_home)
    try:
        rows = fetch_all(
            connection,
            """
            SELECT sessions.raw_session_id,
                   agent_runs.agent_run_key,
                   agent_runs.parent_agent_run_key,
                   agent_runs.agent_kind,
                   agent_runs.agent_name,
                   agent_runs.agent_role,
                   agent_runs.lineage_key,
                   agent_runs.requested_model_id,
                   agent_runs.model_id,
                   agent_runs.lineage_status,
                   agent_runs.lineage_confidence
            FROM agent_runs
            JOIN provider_sessions AS sessions
              ON sessions.session_key = agent_runs.session_key
            ORDER BY sessions.raw_session_id, agent_runs.lineage_key
            """,
        )
        child_event = connection.execute(
            """
            SELECT usage_events.agent_run_key
            FROM usage_events
            JOIN provider_sessions AS sessions
              ON sessions.session_key = usage_events.session_key
            WHERE sessions.raw_session_id = 'child-session'
              AND usage_events.payload_type = 'token_count'
            """
        ).fetchone()
    finally:
        connection.close()

    child_agent_run_key = rows[0][1]
    parent_root_agent_run_key = rows[1][1]
    parent_spawn_agent_run_key = rows[2][1]
    assert rows == [
        (
            "child-session",
            child_agent_run_key,
            parent_spawn_agent_run_key,
            "subagent",
            "Researcher",
            "research_worker",
            "session",
            "gpt-5.4-mini",
            "gpt-5.4",
            "resolved",
            "exact_spawn_match",
        ),
        (
            "parent-session",
            parent_root_agent_run_key,
            None,
            "root",
            "primary",
            "root",
            "root",
            "gpt-5.4",
            "gpt-5.4",
            "root_placeholder",
            "placeholder",
        ),
        (
            "parent-session",
            parent_spawn_agent_run_key,
            parent_root_agent_run_key,
            "subagent",
            "Researcher",
            "research_worker",
            "spawn:child-session",
            "gpt-5.4-mini",
            None,
            "resolved",
            "exact_spawn_match",
        ),
    ]
    assert child_event == (child_agent_run_key,)


def test_root_placeholder_retained_when_no_lineage_evidence_exists(tmp_path: Path) -> None:
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
            SELECT lineage_key, parent_agent_run_key, agent_kind, agent_name, agent_role,
                   lineage_status
            FROM agent_runs
            """
        ).fetchone()
    finally:
        connection.close()

    assert row == ("root", None, "root", "primary", "root", "root_placeholder")


def test_fixture_files_are_sanitized() -> None:
    fixture_dir = fixture_path("sample_rollout.jsonl").parent
    forbidden_fragments = ("/Users/", "\\Users\\", "markhardy")
    for path in sorted(fixture_dir.iterdir()):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        assert all(fragment not in content for fragment in forbidden_fragments), path.name


def _fetch_single_workspace(connection: object) -> WorkspaceRecord:
    row = connection.execute(
        """
        SELECT workspace_key, raw_cwd, resolved_root_path, resolved_root_path_hash,
               display_label, redacted_display_label, resolution_strategy
        FROM workspaces
        """
    ).fetchone()
    assert row is not None
    return WorkspaceRecord(
        workspace_key=str(row[0]),
        raw_cwd=row[1] if row[1] is None else str(row[1]),
        resolved_root_path=str(row[2]),
        resolved_root_path_hash=str(row[3]),
        display_label=str(row[4]),
        redacted_display_label=str(row[5]),
        resolution_strategy=str(row[6]),
    )


def _write_rollout(path: Path, records: list[dict[str, object]]) -> None:
    payload = "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n"
    path.write_text(payload, encoding="utf-8")
