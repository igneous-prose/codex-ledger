# Release Readiness

This checklist is the concrete v0.1.1 readiness gate for the current repository state.

## Required Checks

- `pipx install codex-ledger` succeeds from a built wheel
- `codex-ledger --help` shows grouped command surfaces for import, pricing, report,
  render, explain, verify, and reconcile
- local fixture sync succeeds and produces a deterministic canonical ledger
- saved report JSON validates against the matching schema under `schemas/reports/`
- repeated report JSON writes are byte-stable for the same DB snapshot and filters
- repeated heatmap PNG renders are stable for the same saved aggregate report JSON
- repeated workspace HTML renders are stable for the same saved workspace report JSON
- default report, explain, and render output stays redacted unless explicitly overridden
- `verify ledger` passes on clean data and fails on intentional mismatches
- `verify reports` passes on clean data and fails on intentional mismatches
- `reconcile reference` surfaces diffs without mutating the ledger
- `.github/workflows/release.yml` is present and builds wheel plus sdist, attaches
  release artifacts, and includes PyPI trusted publishing scaffolding without performing
  an automatic release by default

## Standard Local Validation

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv build
uv run ruff format --check .
```

## Manual Smoke Flow

```bash
uv run codex-ledger doctor
uv run codex-ledger sync
uv run codex-ledger price recalc --rule-set reference_usd_openai_standard_2026_04_07
uv run codex-ledger report aggregate --period month --as-of 2026-04-30 --output ./artifacts/aggregate.json
uv run codex-ledger render heatmap --report ./artifacts/aggregate.json --output ./artifacts/aggregate.png
uv run codex-ledger verify ledger
uv run codex-ledger verify reports
```
