# Data Model

The canonical v1 ledger is event-level and SQLite-backed.

Phase 2 keeps the same event-level canonical ledger and extends the workspace and
lineage surfaces. The canonical tables are:

- `import_batches`
- `raw_files`
- `provider_sessions`
- `agent_runs`
- `usage_events`
- `workspaces`
- `workspace_aliases`
- `models`

`usage_events` is the source of truth for imported activity. Daily or workspace rollups
are intentionally not stored as canonical tables.

`workspaces` stores:

- observed `raw_cwd` when available
- `resolved_root_path`
- `resolved_root_path_hash`
- `workspace_key`
- `display_label`
- `redacted_display_label`
- `resolution_strategy`

Current workspace resolution strategies are:

- `project_root_marker`
- `git_root`
- `raw_cwd`
- `unknown`

`agent_runs` stores one primary run per imported session file in the current implementation.
When local session evidence shows a spawned child thread, the child run records
`raw_parent_agent_run_id` and later resolves `parent_agent_run_key` once the parent
session is present in the ledger.
