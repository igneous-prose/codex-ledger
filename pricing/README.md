# Pricing Rules

Phase 3 adds repo-tracked, offline pricing rules under `pricing/rules/`.

The pricing layer in this phase is intentionally narrow:

- event-level only
- deterministic and reproducible
- based on observed execution models
- labeled as `reference_usd`, not invoice parity

Rule files are versioned JSON documents. Each rule set declares:

- `rule_set_id`
- `pricing_plane`
- `currency`
- effective date windows
- token-field mapping
- provider/model match rules
- provenance metadata
- confidence and stability flags

The seeded rule set is conservative and only covers explicitly configured models. Unknown
or preview models remain unsupported until a rule file opts into a rate.

Use:

```bash
codex-ledger price recalc --rule-set reference_usd_openai_standard_2026_04_07
codex-ledger price coverage --rule-set reference_usd_openai_standard_2026_04_07
```
