# Codex Ledger

Codex Ledger is a local-first, auditable usage ledger for Codex. It imports local
session artifacts into a deterministic SQLite-backed ledger and produces privacy-safe,
reproducible derived outputs.

## Status

This repository is currently at Phase 0 of the implementation brief: scaffold,
guardrails, minimal CLI, migration runner skeleton, CI, and documentation stubs are in
place. Import, normalization, pricing, and report generation land in later phases.

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

Inspect the current local environment:

```bash
uv run codex-ledger doctor
```

Initialize the archive-home directory structure and apply the initial migration:

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

The ledger architecture is designed to retain raw provenance locally while defaulting
public outputs to redacted workspace labels. Phase 0 does not generate public reports
yet, but the docs and command surface are structured around that default.

## Examples

```bash
uv run codex-ledger --help
uv run codex-ledger doctor --json
uv run codex-ledger migrate --database /tmp/codex-ledger.sqlite3
```

## Supported data sources

The v1 design targets:

- `~/.codex/sessions/**`
- `~/.codex/archived_sessions/**`
- user-provided JSON backfill files
- user-provided machine-readable Codex JSON outputs

Phase 0 only scaffolds the project and does not ingest these sources yet.

## Scope

The first public release is a Python CLI with a SQLite canonical store, immutable raw
artifact archiving, versioned pricing provenance, static reports, and explainability
commands. Interactive web UI work is explicitly out of scope for v1.
