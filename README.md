# Codex Ledger

Codex Ledger is a local-first, auditable CLI for importing Codex session artifacts into
an event-level SQLite ledger, deriving deterministic reports, estimating reference USD
cost at the event level, and rendering privacy-safe delivery artifacts from saved report
JSON.

## Status

The repository now includes the full v1 delivery layer:

- immutable raw artifact archiving and canonical event ledger
- workspace resolution with redaction, alias, and full-path modes
- agent and subagent lineage plus observability diagnostics
- deterministic event-level reference USD pricing
- aggregate, workspace, agent, and explain report surfaces
- deterministic report JSON artifact writing
- offline schema validation for saved report JSON
- PNG heatmap and static workspace HTML rendering from saved report JSON
- read-only verify and reconcile diagnostics
- release workflow scaffolding and release-readiness docs

## Install

Local development uses `uv` and Python 3.12+.

```bash
uv sync --group dev
uv run codex-ledger --help
```

For local wheel validation before publication:

```bash
uv build
pipx install ./dist/codex_ledger-0.1.0-py3-none-any.whl
```

After the package is actually published, the intended install surface is:

```bash
pipx install codex-ledger
```

## Quickstart

Inspect the current environment and migration status:

```bash
codex-ledger doctor
```

Import locally persisted rollout files:

```bash
codex-ledger sync
```

Import an explicit Codex JSON file:

```bash
codex-ledger import codex-json --input ./sample-report.json
```

Recalculate reference USD pricing:

```bash
codex-ledger price recalc --rule-set reference_usd_openai_standard_2026_04_07
```

Write a deterministic aggregate report JSON artifact:

```bash
codex-ledger report aggregate \
  --period month \
  --as-of 2026-04-30 \
  --output ./artifacts/aggregate.json
```

Render a heatmap from the saved report JSON:

```bash
codex-ledger render heatmap \
  --report ./artifacts/aggregate.json \
  --output ./artifacts/aggregate.png
```

Render static workspace HTML from a workspace report JSON artifact:

```bash
codex-ledger report workspace \
  --period month \
  --as-of 2026-04-30 \
  --output ./artifacts/workspaces.json

codex-ledger render workspace-html \
  --report ./artifacts/workspaces.json \
  --output ./artifacts/workspaces.html
```

Run read-only verification:

```bash
codex-ledger verify ledger
codex-ledger verify reports
```

Compare a reference summary against current derived totals:

```bash
codex-ledger reconcile reference --input ./reference-summary.json
```

## Privacy Defaults

The ledger keeps raw provenance locally, including original source paths in SQLite.
Default outward-facing report, explain, and render output stays redacted unless you
explicitly request `--redaction-mode alias` or `--redaction-mode full`.
Default text output for `sync`, `import codex-json`, `doctor`, and `migrate` also
avoids absolute local paths unless you opt in with `--show-full-paths`.

Rendered PNG and HTML artifacts are traced by sidecar JSON manifests that record report
schema version, generator version, pricing rule-set selection, redaction mode, and
source-report hashes without leaking absolute source paths by default.

## Verification

The standard local verification suite is:

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv build
uv run ruff format --check .
```

Release-readiness details live in [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md).

## Scope

The v1 scope is a Python CLI with deterministic local storage, pricing, reporting,
rendering, explainability, and verification. Interactive web UI work is out of scope.
