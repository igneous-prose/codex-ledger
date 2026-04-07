# Architecture

Codex Ledger keeps one canonical current-state path:

1. discover local rollout files from `~/.codex/sessions/**` and `~/.codex/archived_sessions/**`
2. copy imported artifacts into the external archive under `raw/` using content-addressed paths
3. write import manifests under `state/import-batches/`
4. normalize parsed rollout data into event-level SQLite tables
5. resolve workspace identity and privacy-safe labels
6. preserve agent and subagent lineage where the local evidence supports it
7. calculate deterministic event-level reference USD estimates from offline rule files
8. derive reports and explain payloads directly from the canonical ledger plus `cost_estimates`
9. write validated report JSON artifacts and render delivery formats from those saved JSON artifacts

## Canonical Storage

The canonical truth remains in:

- `import_batches`
- `raw_files`
- `provider_sessions`
- `agent_runs`
- `usage_events`
- `workspaces`
- `models`
- `pricing_rule_sets`
- `cost_estimates`

No canonical aggregate tables are introduced. Aggregate, workspace, agent, explain,
verify, and reconcile surfaces are all derived by read-only joins and queries.

## Workspace Resolution

Workspace resolution prefers:

1. a configured project root marker such as `pyproject.toml`
2. a detected `.git` root
3. the observed cwd itself
4. `unknown` when no cwd is present

The `workspaces` table preserves both the observed input and the resolved identity:

- `raw_cwd`
- `resolved_root_path`
- `resolved_root_path_hash`
- `workspace_key`
- `display_label`
- `redacted_display_label`
- `resolution_strategy`

## Privacy Layer

The privacy layer is applied at report, explain, and render time:

- `redacted` is the default outward-facing mode
- `alias` uses a locally stored alias when present
- `full` is opt-in only

Absolute paths remain ledger-internal by default. Saved report artifacts, rendered HTML,
rendered PNG sidecars, and default CLI output do not need to expose absolute workspace
paths.

## Agent Lineage

Lineage derivation remains conservative:

- one primary `agent_runs` row exists per imported session file
- zero-event spawn-intent rows are preserved when local rollout events explicitly record a child spawn
- exact parent/child repair runs when later child-session evidence arrives
- unresolved spawn-only and orphan child states remain explicit instead of being force-matched

## Pricing Layer

Pricing is layered on top of the canonical event ledger:

1. import and normalize source artifacts into `usage_events`
2. load one repo-tracked pricing rule set
3. match each event by provider, observed model, and event timestamp
4. write one `cost_estimates` row per event, rule set, and pricing plane
5. derive workspace, session, model, and agent totals later by rollup from priced events

Rule-set selection in reports is deterministic:

1. if `--rule-set` is provided, use it
2. otherwise select the latest stable local rule set
3. if no stable local rule set exists, omit cost explicitly

Cost-bearing reports always surface:

- the selected rule set or explicit omission reason
- priced versus unpriced token totals
- a coverage status of `full`, `partial`, `none`, `no_events`, or `omitted`
- warnings when the reference USD estimate is incomplete

## Delivery Layer

Phase 5 completes the v1 delivery surfaces:

- report commands can write deterministic JSON artifacts
- saved report JSON is validated against the matching schema before render time
- heatmap PNG rendering consumes aggregate report JSON only
- static workspace HTML rendering consumes workspace report JSON only
- render sidecars trace each artifact back to the source report hash, schema version,
  generator version, redaction mode, and pricing coverage metadata
- `verify ledger` and `verify reports` provide read-only diagnostic consistency checks
- `reconcile reference` compares a generic reference summary against current derived totals
