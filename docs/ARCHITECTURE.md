# Architecture

Phase 3 keeps the Phase 1 and Phase 2 import path, preserves the Phase 2.1 observability
layer, and adds deterministic event-level pricing:

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
- expose grouped CLI entrypoints for `sync` and `import codex-json`

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

Phase 3 does not create canonical aggregate pricing tables. Unknown or unsupported pricing
is preserved as an explicit estimate state rather than an invented numeric value.
