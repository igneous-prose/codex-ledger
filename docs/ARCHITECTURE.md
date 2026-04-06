# Architecture

Phase 2 keeps the Phase 1 import path and deepens the normalizer:

- discover local rollout files from `~/.codex/sessions/**` and `~/.codex/archived_sessions/**`
- copy source artifacts into the external archive under `raw/` using content-addressed paths
- write import manifests to `state/import-batches/`
- normalize parsed rollout data into event-level SQLite tables
- resolve workspace identity from observed cwd inputs
- preserve parent and child agent lineage when local session evidence supports it
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

Lineage derivation is conservative. The normalizer creates one primary `agent_runs` row
per imported session file, promotes child sessions when session metadata shows spawned
thread evidence, and keeps the deterministic root placeholder only when finer lineage
cannot be justified from the source data.
