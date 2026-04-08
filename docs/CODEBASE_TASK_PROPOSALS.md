# Codebase Task Proposals (2026-04-08)

This audit proposes one scoped task in each requested category.

## 1) Typo Fix Task

**Issue found:** Migration `0004_phase21_agent_observability.sql` uses `phase21` in its filename while the rest of the repository consistently uses `phase2.1` terminology for this feature set.

**Evidence:**
- Migration filename uses `phase21`: `src/codex_ledger/migrations/0004_phase21_agent_observability.sql`.
- Report schema and docs use `phase2.1`: `phase2.1-agent-diagnostics-v1`.

**Proposed task:**
- Normalize naming to avoid the apparent typo/ambiguity (`phase21` vs `phase2.1`) in developer-facing references.
- If the migration filename is renamed, update migration-name displays and tests that currently assert the existing filename. Migration application itself is keyed by version, but `schema_migrations.name`, doctor output, and migration-list tests still surface the filename.

## 2) Bug Fix Task

**Issue found:** Default CLI import/sync output can print absolute source paths, which leaks local filesystem details by default.

**Evidence:**
- `run_sync` and `run_import_codex_json` print `outcome.source_path` directly in CLI output.
- `_import_candidate` resolves source paths to absolute paths.

**Proposed task:**
- Redact or relativize source paths in default CLI output (`sync` and `import codex-json`) and add an opt-in flag for full paths if needed.
- Keep canonical/raw absolute paths only in internal storage where required.

## 3) Comment/Documentation Discrepancy Task

**Issue found:** Privacy documentation says default CLI output does not emit absolute paths, but current `sync`, `import codex-json`, and `doctor` output still does.

**Evidence:**
- `docs/PRIVACY.md` states default CLI output does not emit absolute paths.
- `run_sync` and `run_import_codex_json` print resolved `outcome.source_path` values in default output.
- `run_doctor` prints archive, database, layout, and source-root paths in default output.

**Proposed task:**
- After deciding which CLI surfaces should redact paths by default, update CLI help text and privacy docs to describe that behavior precisely and call out any explicit opt-in for full paths.

## 4) Test Improvement Task

**Issue found:** There is no explicit regression test guaranteeing that default CLI output avoids absolute path leakage on the path-printing surfaces identified above.

**Proposed task:**
- Add integration tests for `codex-ledger sync`, `codex-ledger import codex-json`, and, if the privacy contract continues to cover it, `codex-ledger doctor`, asserting:
  - default output does not contain absolute paths,
  - redacted/relative identifiers are shown,
  - any future opt-in `--show-full-paths` mode (if implemented) is the only code path allowed to print absolute paths.
