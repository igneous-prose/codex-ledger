# Pricing Provenance

Phase 3 adds deterministic, offline pricing based on canonical `usage_events`.

## Scope

- The stable public estimate in this phase is a `reference_usd` estimate.
- Pricing is event-level only. `agent_runs` do not receive independent cost rows.
- Agent, workspace, model, and session totals are derived later by rollup from priced
  events.
- Unknown or unsupported pricing remains explicit. It is never silently coerced to zero.

## Observed Versus Requested Model

- The default pricing key is `usage_events.model_id`.
- `agent_runs.requested_model_id` is preserved for diagnostics and provenance only.
- Phase 3 does not silently substitute a requested model when the observed model is
  missing or unsupported.

## Rule Sets

Repo-tracked rule sets live under `pricing/rules/`.

Each rule set declares:

- a stable `rule_set_id`
- a `pricing_plane`
- a `currency`
- effective date windows
- token-field mapping rules
- model/provider matching rules
- provenance metadata
- confidence and stability labels

The seeded Phase 3 rule set is conservative and only covers explicitly configured models.
Preview or unsupported models remain unsupported until a rule file chooses a rate.

## Cost Estimate Rows

`cost_estimates` stores one row per `usage_events` record, rule set, and pricing plane.

Each row carries:

- `event_id`
- `rule_set_id`
- `pricing_plane`
- `currency`
- `amount`
- `confidence`
- `estimate_status`
- `explanation_json`
- `computed_at_utc`

`explanation_json` is the provenance payload for later explainability. It records the
observed model, requested model, matched rule when present, token counts used for billing,
and the explicit reason when pricing is unsupported or unknown.

## Report and Render Provenance

Phase 4 and Phase 5 keep pricing provenance visible at delivery time:

- reports record selected rule set, selection mode, coverage status, priced token totals,
  unpriced token totals, warnings, and the reference USD estimate when available
- explain payloads trace priced and unpriced totals back to sessions, raw artifacts,
  models, workspaces, and individual events
- render sidecars carry the source-report hash, report schema version, report generator
  version, selected redaction mode, selected pricing rule set, and pricing coverage summary

## Determinism

- Pricing rules are loaded from repo-tracked JSON files only.
- No runtime network lookups are performed.
- Repricing with the same rule set is idempotent.
- Canonical `usage_events` are not mutated during repricing.
