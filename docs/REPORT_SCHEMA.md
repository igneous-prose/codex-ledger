# Report Schema

Report JSON schemas will live in `schemas/reports/` and every emitted report will carry
a schema version and provenance fields.

Phase 4 adds user-facing report and explain payloads on top of the existing agent and
pricing diagnostics.

Current diagnostic schema version:

- `phase4-aggregate-report-v1`
- `phase4-workspace-report-v1`
- `phase2.1-agent-diagnostics-v1`
- `phase4-explain-report-v1`
- `phase3-pricing-coverage-v1`

Aggregate reports include:

- filters and UTC period bounds
- deterministic rule-set selection metadata
- pricing coverage and completeness
- selected-period totals
- totals by model
- totals by account when a source-level account label is available
- per-day buckets and top models
- unsupported or unknown model summaries when they materially affect coverage

Workspace reports include:

- workspace label in the selected redaction mode
- token totals
- priced versus unpriced token totals when pricing is included
- reference USD estimate and coverage status
- top model
- session and agent-run counts
- first and last seen timestamps

Agent diagnostics include:

- UTC period bounds
- root versus subagent usage totals
- usage by agent name
- usage by agent role
- usage by requested model
- usage by observed model
- heavy hitters by tokens
- heavy hitters by reference USD when pricing is included
- workspace spread by agent
- lineage status and confidence mixes
- pricing coverage and completeness when a rule set is selected or defaulted

Explain payloads trace a day, workspace, model, or agent run back to:

- session identity
- stored raw artifact relpath
- requested and observed models
- lineage status and confidence
- workspace attribution using the selected redaction mode
- event-level token totals
- priced versus unpriced token totals when pricing is included
- estimate-status mix and unsupported reasons when relevant

The pricing coverage payload includes:

- priced versus unpriced event counts
- priced versus unpriced token totals
- priced reference USD total
- unsupported or unknown pricing reasons by observed model
- priced coverage by workspace
- priced coverage by model
- priced coverage by agent run

Lightweight JSON Schema files now live under `schemas/reports/`.
