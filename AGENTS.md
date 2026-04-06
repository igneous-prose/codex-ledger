# AGENTS.md

## Mission
Build a trustworthy, local-first usage ledger for Codex with reproducible reports.

## Hard rules
- Do not fork or copy third-party repositories.
- Do not add runtime dependencies unless justified in code review.
- Do not store real user data in the repository.
- Do not expose absolute paths in public outputs by default.
- Do not treat daily rollups as canonical data.
- Do not infer reasoning effort from token counts.
- Do not merge model ids and workspace labels.
- Do not change the SQLite schema without a migration and fixture updates.
- Do not add a web frontend in v1.
- Do not mark credit estimates as stable without explicit pricing-plane support.

## Engineering rules
- Prefer explicit SQL over ORM abstractions.
- Prefer deterministic renderers and stable JSON ordering.
- Every parser path must have fixture tests.
- Every report JSON must carry a schema version.
- Every cost estimate must carry provenance.
- Tests must run without network access.
- Sanitized fixtures only.
- Stop after each milestone and report status before moving on.

## Commands
- lint: `uv run ruff check .`
- format: `uv run ruff format .`
- typecheck: `uv run mypy src`
- test: `uv run pytest`
