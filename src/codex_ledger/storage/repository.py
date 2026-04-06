from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from codex_ledger import __version__
from codex_ledger.domain.records import (
    AgentRunRecord,
    ProviderSessionRecord,
    UsageEventRecord,
    WorkspaceRecord,
)
from codex_ledger.utils.json import canonical_json
from codex_ledger.utils.time import utc_now_iso


@dataclass(frozen=True)
class RawFileRow:
    raw_file_id: str
    content_hash: str
    stored_relpath: str
    parse_status: str


def create_import_batch(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
    provider: str,
    host: str,
    source_kind: str,
    full_backfill: bool,
) -> None:
    connection.execute(
        """
        INSERT INTO import_batches (
            batch_id,
            provider,
            host,
            source_kind,
            importer_version,
            started_at_utc,
            full_backfill,
            manifest_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            provider,
            host,
            source_kind,
            __version__,
            utc_now_iso(),
            int(full_backfill),
            canonical_json({}),
        ),
    )


def finish_import_batch(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
    manifest_relpath: str,
    scanned_file_count: int,
    imported_file_count: int,
    skipped_file_count: int,
    failed_file_count: int,
    manifest_json: str,
) -> None:
    connection.execute(
        """
        UPDATE import_batches
        SET completed_at_utc = ?,
            manifest_relpath = ?,
            scanned_file_count = ?,
            imported_file_count = ?,
            skipped_file_count = ?,
            failed_file_count = ?,
            manifest_json = ?
        WHERE batch_id = ?
        """,
        (
            utc_now_iso(),
            manifest_relpath,
            scanned_file_count,
            imported_file_count,
            skipped_file_count,
            failed_file_count,
            manifest_json,
            batch_id,
        ),
    )


def pending_migration_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM schema_migrations
        ORDER BY version
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def fetch_raw_file_by_hash(
    connection: sqlite3.Connection,
    *,
    provider: str,
    source_kind: str,
    content_hash: str,
) -> RawFileRow | None:
    row = connection.execute(
        """
        SELECT raw_file_id, content_hash, stored_relpath, parse_status
        FROM raw_files
        WHERE provider = ? AND source_kind = ? AND content_hash = ?
        """,
        (provider, source_kind, content_hash),
    ).fetchone()
    if row is None:
        return None
    return RawFileRow(
        raw_file_id=str(row[0]),
        content_hash=str(row[1]),
        stored_relpath=str(row[2]),
        parse_status=str(row[3]),
    )


def insert_raw_file(
    connection: sqlite3.Connection,
    *,
    raw_file_id: str,
    batch_id: str,
    provider: str,
    host: str,
    source_kind: str,
    original_path: str,
    original_path_hash: str,
    content_hash: str,
    size_bytes: int,
    stored_relpath: str,
    parse_status: str,
    parse_error: str | None,
    line_count: int,
    event_count: int,
) -> None:
    timestamp = utc_now_iso()
    connection.execute(
        """
        INSERT INTO raw_files (
            raw_file_id,
            batch_id,
            provider,
            host,
            source_kind,
            original_path,
            original_path_hash,
            content_hash,
            size_bytes,
            stored_relpath,
            parse_status,
            parse_error,
            copied_at_utc,
            imported_at_utc,
            line_count,
            event_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            raw_file_id,
            batch_id,
            provider,
            host,
            source_kind,
            original_path,
            original_path_hash,
            content_hash,
            size_bytes,
            stored_relpath,
            parse_status,
            parse_error,
            timestamp,
            timestamp,
            line_count,
            event_count,
        ),
    )


def upsert_workspace(connection: sqlite3.Connection, workspace: WorkspaceRecord) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO workspaces (
            workspace_key,
            raw_cwd,
            resolved_root_path,
            resolved_root_path_hash,
            display_label,
            redacted_display_label,
            resolution_strategy,
            first_seen_at_utc,
            last_seen_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_key) DO UPDATE SET
            raw_cwd = COALESCE(workspaces.raw_cwd, excluded.raw_cwd),
            resolved_root_path = excluded.resolved_root_path,
            display_label = excluded.display_label,
            redacted_display_label = excluded.redacted_display_label,
            resolution_strategy = excluded.resolution_strategy,
            last_seen_at_utc = excluded.last_seen_at_utc
        """,
        (
            workspace.workspace_key,
            workspace.raw_cwd,
            workspace.resolved_root_path,
            workspace.resolved_root_path_hash,
            workspace.display_label,
            workspace.redacted_display_label,
            workspace.resolution_strategy,
            now,
            now,
        ),
    )


def upsert_workspace_alias(
    connection: sqlite3.Connection,
    *,
    workspace_key: str,
    alias_label: str,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO workspace_aliases (
            workspace_key,
            alias_label,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(workspace_key) DO UPDATE SET
            alias_label = excluded.alias_label,
            updated_at_utc = excluded.updated_at_utc
        """,
        (workspace_key, alias_label, now, now),
    )


def fetch_workspace_alias_map(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT workspace_key, alias_label
        FROM workspace_aliases
        ORDER BY workspace_key
        """
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def upsert_model(connection: sqlite3.Connection, *, model_id: str, provider: str) -> None:
    now = utc_now_iso()
    family = model_id.split("/", maxsplit=1)[-1].split("-", maxsplit=1)[0]
    connection.execute(
        """
        INSERT INTO models (
            model_id,
            provider,
            family,
            supports_reasoning,
            metadata_json,
            first_seen_at_utc,
            last_seen_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(model_id) DO UPDATE SET
            provider = excluded.provider,
            family = excluded.family,
            last_seen_at_utc = excluded.last_seen_at_utc
        """,
        (model_id, provider, family, None, canonical_json({}), now, now),
    )


def upsert_provider_session(
    connection: sqlite3.Connection,
    *,
    session: ProviderSessionRecord,
    batch_id: str,
    raw_file_id: str,
    provider: str,
    host: str,
    source_kind: str,
    source_path: str,
    content_hash: str,
    parse_status: str,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO provider_sessions (
            session_key,
            provider,
            host,
            raw_session_id,
            import_batch_id,
            raw_file_id,
            source_kind,
            source_path,
            content_hash,
            parse_status,
            session_meta_json,
            session_started_at_utc,
            session_ended_at_utc,
            raw_session_started_at,
            session_cwd,
            originator,
            cli_version,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, host, raw_session_id) DO UPDATE SET
            session_meta_json = excluded.session_meta_json,
            session_started_at_utc = COALESCE(
                provider_sessions.session_started_at_utc,
                excluded.session_started_at_utc
            ),
            session_ended_at_utc = CASE
                WHEN provider_sessions.session_ended_at_utc IS NULL
                    THEN excluded.session_ended_at_utc
                WHEN excluded.session_ended_at_utc IS NULL
                    THEN provider_sessions.session_ended_at_utc
                WHEN excluded.session_ended_at_utc > provider_sessions.session_ended_at_utc
                    THEN excluded.session_ended_at_utc
                ELSE provider_sessions.session_ended_at_utc
            END,
            session_cwd = COALESCE(provider_sessions.session_cwd, excluded.session_cwd),
            originator = COALESCE(provider_sessions.originator, excluded.originator),
            cli_version = COALESCE(provider_sessions.cli_version, excluded.cli_version),
            updated_at_utc = excluded.updated_at_utc
        """,
        (
            session.session_key,
            provider,
            host,
            session.raw_session_id,
            batch_id,
            raw_file_id,
            source_kind,
            source_path,
            content_hash,
            parse_status,
            session.session_meta_json,
            session.session_started_at_utc,
            session.session_ended_at_utc,
            session.raw_session_started_at,
            session.session_cwd,
            session.originator,
            session.cli_version,
            now,
            now,
        ),
    )


def upsert_agent_run(
    connection: sqlite3.Connection,
    *,
    agent_run: AgentRunRecord,
    batch_id: str,
    raw_file_id: str,
    source_kind: str,
    source_path: str,
    content_hash: str,
    parse_status: str,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO agent_runs (
            agent_run_key,
            session_key,
            lineage_key,
            import_batch_id,
            raw_file_id,
            source_kind,
            source_path,
            content_hash,
            parse_status,
            parent_agent_run_key,
            raw_parent_agent_run_id,
            agent_kind,
            agent_name,
            agent_role,
            requested_model_id,
            model_id,
            lineage_status,
            lineage_confidence,
            unresolved_reason,
            started_at_utc,
            ended_at_utc,
            raw_metadata_json,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_key, lineage_key) DO UPDATE SET
            parent_agent_run_key = COALESCE(
                excluded.parent_agent_run_key,
                agent_runs.parent_agent_run_key
            ),
            raw_parent_agent_run_id = COALESCE(
                excluded.raw_parent_agent_run_id,
                agent_runs.raw_parent_agent_run_id
            ),
            agent_kind = COALESCE(agent_runs.agent_kind, excluded.agent_kind),
            agent_name = COALESCE(agent_runs.agent_name, excluded.agent_name),
            agent_role = COALESCE(agent_runs.agent_role, excluded.agent_role),
            requested_model_id = COALESCE(
                agent_runs.requested_model_id,
                excluded.requested_model_id
            ),
            model_id = COALESCE(agent_runs.model_id, excluded.model_id),
            lineage_status = excluded.lineage_status,
            lineage_confidence = excluded.lineage_confidence,
            unresolved_reason = excluded.unresolved_reason,
            started_at_utc = COALESCE(agent_runs.started_at_utc, excluded.started_at_utc),
            ended_at_utc = CASE
                WHEN agent_runs.ended_at_utc IS NULL THEN excluded.ended_at_utc
                WHEN excluded.ended_at_utc IS NULL THEN agent_runs.ended_at_utc
                WHEN excluded.ended_at_utc > agent_runs.ended_at_utc THEN excluded.ended_at_utc
                ELSE agent_runs.ended_at_utc
            END,
            raw_metadata_json = excluded.raw_metadata_json,
            updated_at_utc = excluded.updated_at_utc
        """,
        (
            agent_run.agent_run_key,
            agent_run.session_key,
            agent_run.lineage_key,
            batch_id,
            raw_file_id,
            source_kind,
            source_path,
            content_hash,
            parse_status,
            agent_run.parent_agent_run_key,
            agent_run.raw_parent_agent_run_id,
            agent_run.agent_kind,
            agent_run.agent_name,
            agent_run.agent_role,
            agent_run.requested_model_id,
            agent_run.model_id,
            agent_run.lineage_status,
            agent_run.lineage_confidence,
            agent_run.unresolved_reason,
            agent_run.started_at_utc,
            agent_run.ended_at_utc,
            agent_run.raw_metadata_json,
            now,
            now,
        ),
    )


def insert_usage_events(
    connection: sqlite3.Connection,
    *,
    events: tuple[UsageEventRecord, ...],
    batch_id: str,
    raw_file_id: str,
    session_key: str | None,
    provider: str,
    host: str,
    source_kind: str,
    source_path: str,
    content_hash: str,
    parse_status: str,
) -> int:
    inserted = 0
    for event in events:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO usage_events (
                event_id,
                import_batch_id,
                raw_file_id,
                session_key,
                agent_run_key,
                provider,
                host,
                source_kind,
                source_path,
                content_hash,
                parse_status,
                event_index,
                source_line,
                event_type,
                payload_type,
                event_ts_utc,
                raw_event_timestamp,
                turn_id,
                turn_index,
                raw_cwd,
                session_cwd,
                workspace_key,
                workspace_strategy,
                model_id,
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_output_tokens,
                total_tokens,
                raw_event_json,
                dedupe_fingerprint
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                event.event_id,
                batch_id,
                raw_file_id,
                session_key,
                event.agent_run_key,
                provider,
                host,
                source_kind,
                source_path,
                content_hash,
                parse_status,
                event.event_index,
                event.source_line,
                event.event_type,
                event.payload_type,
                event.event_ts_utc,
                event.raw_event_timestamp,
                event.turn_id,
                event.turn_index,
                event.raw_cwd,
                event.session_cwd,
                event.workspace.workspace_key,
                event.workspace.resolution_strategy,
                event.model_id,
                event.input_tokens,
                event.cached_input_tokens,
                event.output_tokens,
                event.reasoning_output_tokens,
                event.total_tokens,
                event.raw_event_json,
                event.event_id,
            ),
        )
        if cursor.rowcount == 1:
            inserted += 1
    return inserted


def repair_agent_run_lineage(connection: sqlite3.Connection) -> int:
    repaired = 0
    cursor = connection.execute(
        """
        UPDATE agent_runs AS child
        SET parent_agent_run_key = (
                SELECT spawn.agent_run_key
                FROM provider_sessions AS parent_session
                JOIN agent_runs AS spawn
                  ON spawn.session_key = parent_session.session_key
                JOIN provider_sessions AS child_session
                  ON child_session.session_key = child.session_key
                WHERE parent_session.raw_session_id = child.raw_parent_agent_run_id
                  AND spawn.lineage_key = 'spawn:' || child_session.raw_session_id
                LIMIT 1
            ),
            requested_model_id = COALESCE(
                (
                    SELECT spawn.requested_model_id
                    FROM provider_sessions AS parent_session
                    JOIN agent_runs AS spawn
                      ON spawn.session_key = parent_session.session_key
                    JOIN provider_sessions AS child_session
                      ON child_session.session_key = child.session_key
                    WHERE parent_session.raw_session_id = child.raw_parent_agent_run_id
                      AND spawn.lineage_key = 'spawn:' || child_session.raw_session_id
                    LIMIT 1
                ),
                child.requested_model_id
            ),
            lineage_status = 'resolved',
            lineage_confidence = 'exact_spawn_match',
            unresolved_reason = NULL
        WHERE child.agent_kind = 'subagent'
          AND child.lineage_key = 'session'
          AND child.raw_parent_agent_run_id IS NOT NULL
          AND EXISTS (
                SELECT 1
                FROM provider_sessions AS parent_session
                JOIN agent_runs AS spawn
                  ON spawn.session_key = parent_session.session_key
                JOIN provider_sessions AS child_session
                  ON child_session.session_key = child.session_key
                WHERE parent_session.raw_session_id = child.raw_parent_agent_run_id
                  AND spawn.lineage_key = 'spawn:' || child_session.raw_session_id
          )
        """
    )
    repaired += int(cursor.rowcount or 0)

    cursor = connection.execute(
        """
        UPDATE agent_runs AS spawn
        SET lineage_status = 'resolved',
            lineage_confidence = 'exact_spawn_match',
            unresolved_reason = NULL
        WHERE spawn.lineage_key LIKE 'spawn:%'
          AND EXISTS (
                SELECT 1
                FROM provider_sessions AS child_session
                WHERE child_session.raw_session_id = SUBSTR(spawn.lineage_key, 7)
          )
        """
    )
    repaired += int(cursor.rowcount or 0)

    cursor = connection.execute(
        """
        UPDATE agent_runs AS child
        SET parent_agent_run_key = (
            SELECT parent_run.agent_run_key
            FROM provider_sessions AS parent_session
            JOIN agent_runs AS parent_run
              ON parent_run.session_key = parent_session.session_key
            WHERE parent_session.raw_session_id = child.raw_parent_agent_run_id
              AND parent_run.lineage_key IN ('session', 'root')
            ORDER BY CASE parent_run.lineage_key WHEN 'session' THEN 0 ELSE 1 END
            LIMIT 1
        ),
            lineage_status = 'resolved',
            lineage_confidence = 'session_metadata_only',
            unresolved_reason = NULL
        WHERE child.agent_kind = 'subagent'
          AND child.lineage_key = 'session'
          AND child.parent_agent_run_key IS NULL
          AND child.raw_parent_agent_run_id IS NOT NULL
          AND EXISTS (
            SELECT 1
            FROM provider_sessions AS parent_session
            JOIN agent_runs AS parent_run
              ON parent_run.session_key = parent_session.session_key
            WHERE parent_session.raw_session_id = child.raw_parent_agent_run_id
              AND parent_run.lineage_key IN ('session', 'root')
          )
        """
    )
    repaired += int(cursor.rowcount or 0)

    cursor = connection.execute(
        """
        UPDATE agent_runs
        SET lineage_status = 'child_only_orphaned',
            lineage_confidence = 'session_metadata_only',
            unresolved_reason = 'parent_session_missing'
        WHERE agent_kind = 'subagent'
          AND lineage_key = 'session'
          AND raw_parent_agent_run_id IS NOT NULL
          AND parent_agent_run_key IS NULL
        """
    )
    repaired += int(cursor.rowcount or 0)

    cursor = connection.execute(
        """
        UPDATE agent_runs
        SET lineage_status = 'spawn_only_unmatched',
            lineage_confidence = 'spawn_event_only',
            unresolved_reason = 'child_session_missing'
        WHERE lineage_key LIKE 'spawn:%'
          AND parent_agent_run_key IS NOT NULL
          AND NOT EXISTS (
                SELECT 1
                FROM provider_sessions AS child_session
                WHERE child_session.raw_session_id = SUBSTR(agent_runs.lineage_key, 7)
          )
        """
    )
    repaired += int(cursor.rowcount or 0)
    return repaired
