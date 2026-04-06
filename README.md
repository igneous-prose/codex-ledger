# Codex Ledger

Codex Ledger is a local-first, auditable usage ledger for Codex. It imports local
session artifacts into a deterministic SQLite-backed ledger and produces privacy-safe,
reproducible derived outputs.

## Status

This repository is currently at Phase 1 of the implementation brief: the scaffold,
canonical SQLite ledger, immutable raw archive, local rollout import, explicit JSON
import, grouped CLI commands, and regression tests are in place. Pricing, reports, HTML,
PNG rendering, and reconciliation remain later-phase work.

## What problem it solves

Codex usage data is scattered across local artifacts. Codex Ledger provides a canonical
ledger with explicit provenance so totals can later be traced back to imported evidence
instead of opaque rollups.

## Install

Local development uses `uv` and Python 3.12+.

```bash
uv sync --group dev
uv run codex-ledger --help
```

When packaged, the intended install surface is `pipx install codex-ledger`.

## Quickstart

Inspect the current local environment and migration status:

```bash
uv run codex-ledger doctor
```

Import locally persisted rollout files:

```bash
uv run codex-ledger sync
```

Import an explicit JSON report file:

```bash
uv run codex-ledger import codex-json --input ./sample-report.json
```

Initialize the archive-home directory structure and apply migrations:

```bash
uv run codex-ledger migrate
```

Run the Phase 0 verification suite:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv build
```

## Privacy defaults

The ledger retains raw provenance locally, including original source paths inside the
SQLite ledger. Public report-generation features are not implemented yet, but workspace
records already include redacted labels so later outputs can default to privacy-safe
presentation.

## Examples

```bash
uv run codex-ledger --help
uv run codex-ledger doctor --json
uv run codex-ledger sync --full-backfill
uv run codex-ledger migrate --database /tmp/codex-ledger.sqlite3
```

## Supported data sources

The v1 design targets:

- `~/.codex/sessions/**`
- `~/.codex/archived_sessions/**`
- user-provided JSON backfill files
- user-provided machine-readable Codex JSON outputs

Phase 1 implements `local_rollout_file` and `imported_json_report`. The other planned
source kinds already exist in schema and command validation, but their import paths are
not implemented yet.

## Scope

The first public release is a Python CLI with a SQLite canonical store, immutable raw
artifact archiving, versioned pricing provenance, static reports, and explainability
commands. Interactive web UI work is explicitly out of scope for v1.
