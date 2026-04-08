# Codebase Task Proposals (2026-04-08)

This audit proposes one scoped task in each requested category.

## 1) Typo Fix Task

**Issue found:** Migration `0004_phase21_agent_observability.sql` uses `phase21` in its filename while the rest of the repository consistently uses `phase2.1` terminology for this feature set.

**Evidence:**
- Migration filename uses `phase21`: `src/codex_ledger/migrations/0004_phase21_agent_observability.sql`.
- Report schema and docs use `phase2.1`: `phase2.1-agent-diagnostics-v1`.

**Proposed task:**
- Normalize naming to avoid the apparent typo/ambiguity (`phase21` vs `phase2.1`) in developer-facing references.
- Keep backwards compatibility for already-applied migration names (do not silently rename a migration that may already be recorded in `schema_migrations`; add an alias/compatibility strategy instead).

## 2) Bug Fix Task

**Issue found:** Default CLI import/sync output can print absolute source paths, which leaks local filesystem details by default.

**Evidence:**
- `run_sync` and `run_import_codex_json` print `outcome.source_path` directly in CLI output.
- `_import_candidate` resolves source paths to absolute paths.

**Proposed task:**
- Redact or relativize source paths in default CLI output (`sync` and `import codex-json`) and add an opt-in flag for full paths if needed.
- Keep canonical/raw absolute paths only in internal storage where required.

## 3) Comment/Documentation Discrepancy Task

**Issue found:** Privacy documentation says default CLI output does not emit absolute paths, but current sync/import output does.

**Evidence:**
- `docs/PRIVACY.md` states default CLI output does not emit absolute paths.
- CLI currently prints resolved `outcome.source_path` values in default output.

**Proposed task:**
- After fixing path redaction behavior, update CLI help text and privacy docs to explicitly describe which commands redact paths by default and how users can opt in to full paths.

## 4) Test Improvement Task

**Issue found:** There is no explicit regression test guaranteeing that default CLI sync/import output avoids absolute path leakage.

**Proposed task:**
- Add integration tests for `codex-ledger sync` and `codex-ledger import codex-json` asserting:
  - default output does not contain absolute paths,
  - redacted/relative identifiers are shown,
  - any future opt-in `--show-full-paths` mode (if implemented) is the only code path allowed to print absolute paths.
