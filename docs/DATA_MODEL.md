# Data Model

The canonical v1 ledger is event-level and SQLite-backed.

Phase 2.1 keeps the same event-level canonical ledger and extends the workspace,
lineage, and agent observability surfaces. The canonical tables are:

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
Phase 2.1 also stores explicit agent observability fields on `agent_runs`:

- `agent_kind`
- `requested_model_id`
- `model_id` as the observed model
- `lineage_status`
- `lineage_confidence`
- `unresolved_reason`

The current canonical lineage states are:

- `resolved`
- `spawn_only_unmatched`
- `child_only_orphaned`
- `root_placeholder`

Parent spawn intents and imported child sessions are both preserved. Spawn rows may have
zero usage events; event-bearing child runs remain the canonical source of subagent usage.
