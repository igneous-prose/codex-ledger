# Data Model

The canonical v1 ledger is event-level and SQLite-backed.

Phase 3 keeps the same event-level canonical ledger and adds deterministic event-level
pricing estimates. The canonical tables are:

- `import_batches`
- `raw_files`
- `provider_sessions`
- `agent_runs`
- `usage_events`
- `pricing_rule_sets`
- `cost_estimates`
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

Phase 3 adds:

- `pricing_rule_sets` for loaded repo-tracked rule metadata
- `cost_estimates` for deterministic event-level reference USD estimates

`cost_estimates` is keyed by `(event_id, rule_set_id, pricing_plane)` and stores:

- `amount` in the declared rule-set currency
- `confidence`
- `estimate_status`
- `explanation_json`
- `computed_at_utc`

Only `usage_events` receive pricing rows. Zero-event spawn placeholders and root
placeholders never receive independent cost rows.

Phase 4 and Phase 5 do not add new canonical aggregate tables. Report payloads, render
sidecars, verify results, and reconcile diffs are all derived from joins across the
existing canonical tables and `cost_estimates`.

The stable report and explain outputs now carry:

- `schema_version`
- deterministic `generated_at_utc`
- `generator_version`
- `filters`
- `timezone`
- a `pricing` block describing rule-set selection and coverage

When pricing is included, reports distinguish:

- total tokens
- priced token totals
- unpriced token totals
- reference USD estimate over priced events only

When pricing is omitted, the report metadata says so explicitly.

Saved report JSON artifacts are validated delivery outputs, not new canonical storage.
Rendered PNG and HTML artifacts also remain non-canonical and are traced by provenance
sidecars rather than embedded back into SQLite.
