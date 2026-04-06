from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from codex_ledger.domain.records import (
    IMPLEMENTED_SOURCE_KINDS,
    ImportBatchSummary,
    ImportCandidate,
    ImportOutcome,
    ParsedFile,
    SourceKind,
)
from codex_ledger.ingest.discovery import discover_local_rollout_files
from codex_ledger.paths import archive_home_layout, ensure_archive_home_layout
from codex_ledger.providers.codex.parser import parse_imported_json_report, parse_local_rollout_file
from codex_ledger.storage.archive import archive_raw_file
from codex_ledger.storage.migrations import (
    apply_migrations,
    connect_database,
    default_database_path,
)
from codex_ledger.storage.repository import (
    create_import_batch,
    fetch_raw_file_by_hash,
    finish_import_batch,
    insert_raw_file,
    insert_usage_events,
    upsert_agent_run,
    upsert_model,
    upsert_provider_session,
    upsert_workspace,
)
from codex_ledger.utils.hashing import sha256_file, sha256_text
from codex_ledger.utils.json import canonical_json
from codex_ledger.utils.time import utc_now_iso


def sync_local_codex(
    *,
    archive_home: Path,
    full_backfill: bool,
) -> tuple[ImportBatchSummary, tuple[ImportOutcome, ...]]:
    return run_import_batch(
        archive_home=archive_home,
        candidates=tuple(discover_local_rollout_files()),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=full_backfill,
    )


def import_codex_json_report(
    *,
    archive_home: Path,
    input_path: Path,
) -> tuple[ImportBatchSummary, tuple[ImportOutcome, ...]]:
    return run_import_batch(
        archive_home=archive_home,
        candidates=(ImportCandidate(source_path=input_path, source_kind="imported_json_report"),),
        provider="codex",
        host="imported_json",
        source_kind="imported_json_report",
        full_backfill=False,
    )


def run_import_batch(
    *,
    archive_home: Path,
    candidates: tuple[ImportCandidate, ...],
    provider: str,
    host: str,
    source_kind: SourceKind,
    full_backfill: bool,
) -> tuple[ImportBatchSummary, tuple[ImportOutcome, ...]]:
    if source_kind not in IMPLEMENTED_SOURCE_KINDS:
        raise ValueError(f"Unsupported source kind for Phase 1: {source_kind}")

    layout = ensure_archive_home_layout(archive_home)
    database_path = default_database_path(archive_home)
    apply_migrations(database_path)

    batch_id = utc_now_iso().replace(":", "").replace("-", "")
    manifest_relpath = f"import-batches/{batch_id}.json"
    outcomes: list[ImportOutcome] = []

    with connect_database(database_path) as connection:
        create_import_batch(
            connection,
            batch_id=batch_id,
            provider=provider,
            host=host,
            source_kind=source_kind,
            full_backfill=full_backfill,
        )

        imported_count = 0
        skipped_count = 0
        failed_count = 0

        for candidate in sorted(candidates, key=lambda item: str(item.source_path)):
            outcome = _import_candidate(
                connection=connection,
                archive_home=archive_home,
                archive_layout=layout,
                batch_id=batch_id,
                candidate=candidate,
                full_backfill=full_backfill,
            )
            outcomes.append(outcome)
            if outcome.status in {"imported", "replayed_existing"}:
                imported_count += 1
            elif outcome.status == "skipped_existing":
                skipped_count += 1
            else:
                failed_count += 1

        manifest_payload = {
            "batch_id": batch_id,
            "provider": provider,
            "host": host,
            "source_kind": source_kind,
            "full_backfill": full_backfill,
            "generated_at_utc": utc_now_iso(),
            "outcomes": [
                {
                    "source_path": str(outcome.source_path),
                    "source_kind": outcome.source_kind,
                    "status": outcome.status,
                    "detail": outcome.detail,
                    "raw_file_id": outcome.raw_file_id,
                    "content_hash": outcome.content_hash,
                    "stored_relpath": outcome.stored_relpath,
                    "event_count": outcome.event_count,
                }
                for outcome in outcomes
            ],
        }
        manifest_path = layout["state"] / manifest_relpath
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(canonical_json(manifest_payload) + "\n", encoding="utf-8")

        finish_import_batch(
            connection,
            batch_id=batch_id,
            manifest_relpath=manifest_relpath,
            scanned_file_count=len(candidates),
            imported_file_count=imported_count,
            skipped_file_count=skipped_count,
            failed_file_count=failed_count,
            manifest_json=canonical_json(manifest_payload),
        )

    summary = ImportBatchSummary(
        batch_id=batch_id,
        manifest_relpath=manifest_relpath,
        scanned_file_count=len(candidates),
        imported_file_count=imported_count,
        skipped_file_count=skipped_count,
        failed_file_count=failed_count,
    )
    return summary, tuple(outcomes)


def _import_candidate(
    *,
    connection: sqlite3.Connection,
    archive_home: Path,
    archive_layout: dict[str, Path],
    batch_id: str,
    candidate: ImportCandidate,
    full_backfill: bool,
) -> ImportOutcome:
    source_path = candidate.source_path.expanduser().resolve(strict=False)
    if not source_path.exists() or not source_path.is_file():
        return ImportOutcome(
            source_path=source_path,
            source_kind=candidate.source_kind,
            status="missing_source",
            detail="source file does not exist",
            raw_file_id=None,
            content_hash=None,
            stored_relpath=None,
            event_count=0,
        )

    content_hash = sha256_file(source_path)
    existing_raw = fetch_raw_file_by_hash(
        connection,
        provider="codex",
        source_kind=candidate.source_kind,
        content_hash=content_hash,
    )
    if existing_raw is not None and not full_backfill:
        return ImportOutcome(
            source_path=source_path,
            source_kind=candidate.source_kind,
            status="skipped_existing",
            detail="content hash already imported",
            raw_file_id=existing_raw.raw_file_id,
            content_hash=content_hash,
            stored_relpath=existing_raw.stored_relpath,
            event_count=0,
        )

    archived_hash, stored_relpath, size_bytes = archive_raw_file(
        archive_layout["raw"],
        source_path,
        "codex",
        candidate.source_kind,
    )
    if archived_hash != content_hash:
        raise ValueError("Raw archive hash mismatch")

    parsed = _parse_candidate(source_path, candidate.source_kind)
    raw_file_id = (
        existing_raw.raw_file_id
        if existing_raw is not None
        else sha256_text(f"raw:{candidate.source_kind}:{content_hash}")[:32]
    )

    if existing_raw is None:
        insert_raw_file(
            connection,
            raw_file_id=raw_file_id,
            batch_id=batch_id,
            provider=parsed.provider,
            host=parsed.host,
            source_kind=candidate.source_kind,
            original_path=str(source_path),
            original_path_hash=sha256_text(str(source_path)),
            content_hash=content_hash,
            size_bytes=size_bytes,
            stored_relpath=stored_relpath,
            parse_status=parsed.parse_status,
            parse_error=parsed.parse_error,
            line_count=parsed.line_count,
            event_count=len(parsed.events),
        )

    if parsed.parse_status != "parsed" or parsed.session is None:
        return ImportOutcome(
            source_path=source_path,
            source_kind=candidate.source_kind,
            status=parsed.parse_status,
            detail=parsed.parse_error,
            raw_file_id=raw_file_id,
            content_hash=content_hash,
            stored_relpath=stored_relpath,
            event_count=0,
        )

    for workspace in parsed.workspaces:
        upsert_workspace(connection, workspace)
    for model_id in parsed.model_ids:
        upsert_model(connection, model_id=model_id, provider=parsed.provider)
    upsert_provider_session(
        connection,
        session=parsed.session,
        batch_id=batch_id,
        raw_file_id=raw_file_id,
        provider=parsed.provider,
        host=parsed.host,
        source_kind=candidate.source_kind,
        source_path=str(source_path),
        content_hash=content_hash,
        parse_status=parsed.parse_status,
    )
    for agent_run in parsed.agent_runs:
        upsert_agent_run(
            connection,
            agent_run=agent_run,
            batch_id=batch_id,
            raw_file_id=raw_file_id,
            source_kind=candidate.source_kind,
            source_path=str(source_path),
            content_hash=content_hash,
            parse_status=parsed.parse_status,
        )
    inserted_events = insert_usage_events(
        connection,
        events=parsed.events,
        batch_id=batch_id,
        raw_file_id=raw_file_id,
        session_key=parsed.session.session_key,
        agent_run_key=parsed.agent_runs[0].agent_run_key if parsed.agent_runs else None,
        provider=parsed.provider,
        host=parsed.host,
        source_kind=candidate.source_kind,
        source_path=str(source_path),
        content_hash=content_hash,
        parse_status=parsed.parse_status,
    )
    status = "replayed_existing" if existing_raw is not None else "imported"
    return ImportOutcome(
        source_path=source_path,
        source_kind=candidate.source_kind,
        status=status,
        detail=None,
        raw_file_id=raw_file_id,
        content_hash=content_hash,
        stored_relpath=stored_relpath,
        event_count=inserted_events,
    )


def _parse_candidate(path: Path, source_kind: SourceKind) -> ParsedFile:
    if source_kind == "local_rollout_file":
        return parse_local_rollout_file(path)
    if source_kind == "imported_json_report":
        return parse_imported_json_report(path)
    raise ValueError(f"Unsupported source kind: {source_kind}")


def summarize_doctor_status(archive_home: Path) -> dict[str, Any]:
    layout = archive_home_layout(archive_home)
    database_path = default_database_path(archive_home)

    source_roots = []
    for path in (
        Path("~/.codex/sessions").expanduser(),
        Path("~/.codex/archived_sessions").expanduser(),
    ):
        matches = sorted(path.rglob("*.jsonl")) if path.exists() else []
        source_roots.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "jsonl_count": len(matches),
            }
        )

    history_status = "enabled"
    if all(not item["exists"] for item in source_roots):
        history_status = "disabled"
    elif all(item["jsonl_count"] == 0 for item in source_roots):
        history_status = "unknown"

    from codex_ledger.storage.migrations import migration_filenames

    raw_file_count = 0
    session_count = 0
    event_count = 0
    failed_raw_count = 0
    applied_migrations: list[str] = []

    if database_path.exists():
        with connect_database(database_path) as connection:
            raw_file_count = _scalar(connection, "SELECT COUNT(*) FROM raw_files")
            session_count = _scalar(connection, "SELECT COUNT(*) FROM provider_sessions")
            event_count = _scalar(connection, "SELECT COUNT(*) FROM usage_events")
            failed_raw_count = _scalar(
                connection,
                "SELECT COUNT(*) FROM raw_files WHERE parse_status <> 'parsed'",
            )
            applied_migrations = _column_values(
                connection, "SELECT name FROM schema_migrations ORDER BY version"
            )

    pending = [name for name in migration_filenames() if name not in applied_migrations]

    return {
        "archive_home": str(archive_home),
        "archive_home_exists": archive_home.exists(),
        "database_path": str(database_path),
        "expected_layout": {name: str(path) for name, path in layout.items()},
        "source_roots": source_roots,
        "history_persistence_status": history_status,
        "migration_status": {
            "applied": applied_migrations,
            "pending": pending,
        },
        "ledger_status": {
            "raw_file_count": raw_file_count,
            "failed_raw_file_count": failed_raw_count,
            "provider_session_count": session_count,
            "usage_event_count": event_count,
        },
    }


def _scalar(connection: sqlite3.Connection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    if row is None:
        return 0
    value = row[0]
    return int(value) if isinstance(value, int) else 0


def _column_values(connection: sqlite3.Connection, sql: str) -> list[str]:
    return [str(row[0]) for row in connection.execute(sql).fetchall()]
