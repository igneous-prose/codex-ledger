# Architecture

Phase 1 establishes the first working data path:

- discover local rollout files from `~/.codex/sessions/**` and `~/.codex/archived_sessions/**`
- copy source artifacts into the external archive under `raw/` using content-addressed paths
- write import manifests to `state/import-batches/`
- normalize parsed rollout data into event-level SQLite tables
- expose grouped CLI entrypoints for `sync` and `import codex-json`

Later phases fill in the layered architecture described in the implementation brief:
collector, normalizer, storage, pricing, reports, render, CLI, and providers.
