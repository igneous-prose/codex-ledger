# Architecture

Phase 4 keeps the Phase 1 to Phase 3 storage path intact and adds read-only report and
explainability surfaces on top of the canonical ledger:

- discover local rollout files from `~/.codex/sessions/**` and `~/.codex/archived_sessions/**`
- copy source artifacts into the external archive under `raw/` using content-addressed paths
- write import manifests to `state/import-batches/`
- normalize parsed rollout data into event-level SQLite tables
- resolve workspace identity from observed cwd inputs
- preserve parent and child agent lineage when local session evidence supports it
- preserve unmatched spawn intents and orphan child sessions as explicit lineage states
- expose read-only agent diagnostics and explainability from canonical SQLite rows
- load versioned pricing rules from repo-tracked JSON files
- calculate event-level reference USD estimates from observed execution models
- expose pricing recalculation and coverage diagnostics without mutating canonical events
- expose grouped CLI entrypoints for aggregate, workspace, agent, and explain reports

Workspace resolution currently prefers:

1. a configured project marker such as `pyproject.toml`
2. a detected `.git` root
3. the observed cwd itself
4. `unknown` when no cwd is present

The privacy layer is still internal-only in this phase. It already supports the output
modes that later report surfaces will use:

- `redacted` by default
- `alias` when a local alias exists for a workspace
- `full` only when a caller explicitly opts into full paths

Lineage derivation remains conservative. The normalizer now creates:

- one primary `agent_runs` row per imported session file
- zero or more spawn-intent `agent_runs` rows when local rollout events explicitly record child spawns

Repair logic resolves exact parent/child matches when the child session appears later, keeps
spawn-only rows unresolved when the child session never arrives, and keeps child-only rows
orphaned when no justified parent link exists yet.

Pricing remains layered on top of the canonical event ledger:

1. import and normalize source artifacts into `usage_events`
2. load one repo-tracked pricing rule set
3. match each event by provider, observed model, and event timestamp
4. write one `cost_estimates` row per event and rule set
5. derive later workspace, session, model, or agent totals by rollup from priced events

Phase 4 still does not create canonical aggregate tables. Reports are built from joins and
queries over `usage_events`, `agent_runs`, `workspaces`, `provider_sessions`, `raw_files`,
and `cost_estimates`.

Rule-set selection in reports is deterministic:

1. if `--rule-set` is provided, use it
2. otherwise select the latest stable local rule set
3. if no stable local rule set exists, omit cost explicitly

Cost-bearing reports always carry:

- the selected rule set or explicit omission reason
- priced versus unpriced token totals
- a coverage status of `full`, `partial`, `none`, `no_events`, or `omitted`
- warnings when the reference USD estimate is incomplete
