# Report Schema

Report JSON schemas will live in `schemas/reports/` and every emitted report will carry
a schema version and provenance fields.

Phase 2.1 introduces a diagnostic JSON payload for `codex-ledger report agents` and
`codex-ledger explain agent`.

Current diagnostic schema version:

- `phase2.1-agent-diagnostics-v1`

The agent diagnostics payload includes:

- UTC period bounds
- root versus subagent usage totals
- usage by agent name
- usage by agent role
- usage by requested model
- usage by observed model
- heavy hitters by tokens
- workspace spread by agent
- lineage status and confidence mixes

The explain payload traces one agent run back to:

- session identity
- stored raw artifact relpath
- requested and observed models
- lineage status and confidence
- workspace attribution using the selected redaction mode
- event-level token totals
