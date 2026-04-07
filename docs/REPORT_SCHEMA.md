# Report Schema

Stable report schemas live under `schemas/reports/`. Saved report artifacts are validated
locally against the matching schema before render time.

Current schema versions:

- `phase4-aggregate-report-v1`
- `phase4-workspace-report-v1`
- `phase2.1-agent-diagnostics-v1`
- `phase4-explain-report-v1`
- `phase3-pricing-coverage-v1`

All stable report payloads carry:

- `schema_version`
- `generated_at_utc`
- `generator_version`
- `filters`
- `timezone`
- `pricing`
- a report-specific data or summary block

## Aggregate Reports

Aggregate reports include:

- UTC period filters and bounds
- deterministic rule-set selection metadata
- pricing coverage and completeness
- selected-period totals
- totals by model
- totals by account when a source-level account label exists
- per-day buckets and top models
- unsupported or unknown model summaries when they materially affect totals

## Workspace Reports

Workspace reports include:

- workspace label in the selected redaction mode
- token totals
- priced versus unpriced token totals when pricing is included
- reference USD estimate and coverage status
- top model
- reasoning-output token totals
- session and agent-run counts
- first and last seen timestamps

## Agent Diagnostics

Agent diagnostics include:

- UTC period bounds
- root versus subagent usage totals
- usage by agent name
- usage by role
- usage by requested model
- usage by observed model
- heavy hitters by tokens and, when available, by reference USD
- workspace spread by agent
- lineage status and confidence mixes
- matched, unresolved-spawn, and orphan-child counts

## Explain Payloads

Explain payloads trace a day, workspace, model, or agent run back to:

- sessions
- stored raw artifact relpaths
- requested and observed models
- lineage status and confidence
- workspace attribution using the selected redaction mode
- event-level token totals
- priced versus unpriced token totals when pricing is included
- estimate-status mix and unsupported reasons when relevant

## Rendering Contract

Phase 5 renderers consume saved report JSON only:

- `render heatmap` accepts aggregate report JSON
- `render workspace-html` accepts workspace report JSON

Render sidecars are not canonical storage, but they carry stable provenance fields for:

- source report name and hash
- report schema version
- report generator version
- report `generated_at_utc`
- selected redaction mode
- selected pricing rule set or explicit `null`
- pricing coverage summary
