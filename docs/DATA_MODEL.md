# Data Model

The canonical v1 ledger is event-level and SQLite-backed.

Phase 1 introduces these canonical tables:

- `import_batches`
- `raw_files`
- `provider_sessions`
- `agent_runs`
- `usage_events`
- `workspaces`
- `models`

`usage_events` is the source of truth for imported activity. Daily or workspace rollups
are intentionally not stored as canonical tables.
