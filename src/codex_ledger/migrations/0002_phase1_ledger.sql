CREATE TABLE import_batches (
    batch_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    host TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (
        source_kind IN (
            'local_rollout_file',
            'imported_json_report',
            'stdout_json_capture',
            'cloud_task_json',
            'reconciliation_reference'
        )
    ),
    importer_version TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    full_backfill INTEGER NOT NULL DEFAULT 0,
    scanned_file_count INTEGER NOT NULL DEFAULT 0,
    imported_file_count INTEGER NOT NULL DEFAULT 0,
    skipped_file_count INTEGER NOT NULL DEFAULT 0,
    failed_file_count INTEGER NOT NULL DEFAULT 0,
    manifest_relpath TEXT,
    manifest_json TEXT NOT NULL
) STRICT;

CREATE TABLE raw_files (
    raw_file_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES import_batches(batch_id),
    provider TEXT NOT NULL,
    host TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    original_path TEXT NOT NULL,
    original_path_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    stored_relpath TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    parse_error TEXT,
    copied_at_utc TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL,
    line_count INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(provider, source_kind, content_hash)
) STRICT;

CREATE TABLE provider_sessions (
    session_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    host TEXT NOT NULL,
    raw_session_id TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(batch_id),
    raw_file_id TEXT NOT NULL REFERENCES raw_files(raw_file_id),
    source_kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    session_meta_json TEXT NOT NULL,
    session_started_at_utc TEXT,
    session_ended_at_utc TEXT,
    raw_session_started_at TEXT,
    session_cwd TEXT,
    originator TEXT,
    cli_version TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(provider, host, raw_session_id)
) STRICT;

CREATE TABLE agent_runs (
    agent_run_key TEXT PRIMARY KEY,
    session_key TEXT NOT NULL REFERENCES provider_sessions(session_key),
    lineage_key TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(batch_id),
    raw_file_id TEXT NOT NULL REFERENCES raw_files(raw_file_id),
    source_kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    parent_agent_run_key TEXT REFERENCES agent_runs(agent_run_key),
    raw_parent_agent_run_id TEXT,
    agent_name TEXT,
    agent_role TEXT,
    model_id TEXT,
    started_at_utc TEXT,
    ended_at_utc TEXT,
    raw_metadata_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(session_key, lineage_key)
) STRICT;

CREATE TABLE workspaces (
    workspace_key TEXT PRIMARY KEY,
    resolved_root_path TEXT NOT NULL,
    resolved_root_path_hash TEXT NOT NULL,
    display_label TEXT NOT NULL,
    redacted_display_label TEXT NOT NULL,
    resolution_strategy TEXT NOT NULL,
    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE models (
    model_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    family TEXT,
    supports_reasoning INTEGER,
    metadata_json TEXT NOT NULL,
    first_seen_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE usage_events (
    event_id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batches(batch_id),
    raw_file_id TEXT NOT NULL REFERENCES raw_files(raw_file_id),
    session_key TEXT REFERENCES provider_sessions(session_key),
    agent_run_key TEXT REFERENCES agent_runs(agent_run_key),
    provider TEXT NOT NULL,
    host TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    event_index INTEGER NOT NULL,
    source_line INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_type TEXT,
    event_ts_utc TEXT,
    raw_event_timestamp TEXT,
    turn_id TEXT,
    turn_index INTEGER,
    raw_cwd TEXT,
    session_cwd TEXT,
    workspace_key TEXT NOT NULL REFERENCES workspaces(workspace_key),
    workspace_strategy TEXT NOT NULL,
    model_id TEXT REFERENCES models(model_id),
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_output_tokens INTEGER,
    total_tokens INTEGER,
    raw_event_json TEXT NOT NULL,
    dedupe_fingerprint TEXT NOT NULL UNIQUE
) STRICT;

CREATE INDEX idx_raw_files_batch_id ON raw_files(batch_id);
CREATE INDEX idx_provider_sessions_raw_file_id ON provider_sessions(raw_file_id);
CREATE INDEX idx_agent_runs_session_key ON agent_runs(session_key);
CREATE INDEX idx_usage_events_session_key ON usage_events(session_key);
CREATE INDEX idx_usage_events_workspace_key ON usage_events(workspace_key);
CREATE INDEX idx_usage_events_event_ts ON usage_events(event_ts_utc);
