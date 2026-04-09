# Changelog

## 0.1.1

- hardened local output writes, including symlink refusal for user-controlled output
  paths while preserving standard system temp-root aliases such as `/tmp` on macOS
- made `doctor --json` privacy-safe by default and added reconcile input size limits
- tightened pricing rule integrity checks so packaged rule data remains authoritative
  and repo mirrors fail fast on drift, emptiness, or unexpected files
- split CI into separate lint, typecheck, test, and build checks for clearer PR status

## 0.1.0

- v1 delivery layer now includes the canonical event ledger, workspace resolution,
  agent/subagent lineage, reference USD pricing, report generation, explainability,
  deterministic saved report JSON, static rendering, verification diagnostics, and
  release workflow scaffolding.
