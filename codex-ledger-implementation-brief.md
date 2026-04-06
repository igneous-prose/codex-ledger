# Codex Ledger — Implementation Brief for Public v1

## How to use this file

This document is the single implementation brief for the initial public release. It is written so an engineering agent can start immediately: create the repository, scaffold the project, implement the architecture, and proceed through the milestones without inventing missing design decisions.

The repository must be a fresh public repository. Do not fork any other project. Do not copy internal caches or third-party code into the repo. No runtime dependency on any other Codex usage tool is allowed.

---

## 1. Product definition

### Working product name
**Codex Ledger**

### One-line position
A local-first, auditable usage ledger for Codex that imports local session artifacts into a deterministic SQLite event store and generates trustworthy static reports.

### Core differentiation
This project is **not** “just another dashboard.”  
The value is:

- immutable raw imports
- canonical event-level ledger
- explicit provenance for every number
- auditable workspace attribution
- deterministic JSON/HTML/PNG outputs
- privacy-safe default outputs
- architecture that can later support multiple providers and hosts without a major rewrite

### Primary user promise
“If a report says I used tokens, cost, or a workspace on a given day, I can trace that number back to imported evidence.”

---

## 2. Executive decisions

These decisions are settled and should not be reopened during v1 implementation:

1. **Fresh public repo, not a fork.**
2. **Build locally on macOS, but put the repo on GitHub from day one.**
3. **Python CLI for v1.**
4. **SQLite is the canonical store.**
5. **The canonical ledger is event-level, not daily-rollup-level.**
6. **Reports are derived artifacts, never the source of truth.**
7. **Workspace attribution is supported in v1.**
8. **Model IDs and workspaces are separate concepts and must never be conflated.**
9. **Absolute paths are stored in the ledger but are redacted in public outputs by default.**
10. **Ship stable token accounting and stable USD estimates in v1.**
11. **Do not present ChatGPT credit estimation as fully stable in v1.**
12. **No interactive web app in v1. Static HTML is acceptable; SPA/frontend stack is out of scope.**
13. **Future expansion to Claude, OpenCode, Zed, and other hosts/providers must be enabled by top-level abstractions now.**

---

## 3. Confirmed findings that must shape the design

### 3.1 Event ledger, not daily tables
The original proposal leaned too heavily on daily usage tables. That is the wrong canonical layer. Daily, weekly, monthly, yearly, workspace, model, and cost reports must all be recomputable from event-level records.

### 3.2 Model IDs are not workspace labels
Strings that look like `provider/model-name` may represent spawned or secondary model usage and must be treated as model identifiers, not workspace names. Keep model usage and workspace attribution fully separate in schema, code, and UI.

### 3.3 Workspace attribution is good enough for v1, but must remain auditable
`turn_context.cwd` should be the primary workspace signal. `session_meta.cwd` is the fallback. Both the raw path and the resolved workspace identity must be preserved so report outputs can be explained.

### 3.4 Pricing must be versioned and provenance-aware
Codex pricing rules have changed over time and differ by plan/rate-card mode. The project must be able to say exactly which pricing rule set produced each estimate.

### 3.5 Local session files are not the only future input
The architecture must support:
- persisted local session files
- imported JSON reports
- machine-readable non-interactive JSON output
- cloud task JSON
- future host/editor session history sources

### 3.6 Privacy is a first-class feature
The ledger may contain sensitive file paths, usernames, workspace names, and project names. Public report outputs must default to redacted or aliased workspace labels.

---

## 4. Scope for v1

### In scope
- import Codex local usage artifacts from:
  - `~/.codex/sessions/**`
  - `~/.codex/archived_sessions/**`
  - user-provided JSON backfill files
  - user-provided machine-readable Codex JSON outputs
- copy raw source artifacts into an immutable archive outside the repo
- normalize imported data into a deduplicated SQLite ledger
- maintain model, workspace, session, agent-run, and event provenance
- generate:
  - aggregate JSON reports
  - workspace JSON reports
  - CLI tables
  - static HTML workspace report
  - PNG heatmap
- explain totals for a given day/workspace/model from underlying ledger events
- ship docs, CI, tests, package metadata, and release workflow

### Out of scope for v1
- interactive browser application
- live usage polling UI
- editing or mutating Codex session history
- cloud-only analytics service
- non-Codex providers as shipping features
- hard dependency on external dashboards or caches
- public guarantee of exact ChatGPT credit billing parity

---

## 5. Build and release strategy

### Development model
- develop locally on macOS
- commit early
- push to GitHub immediately
- use GitHub for CI, issue tracking, and release history
- do not wait until the project is “finished” before creating the repo

### Recommended repo name
Use one of these:
- `codex-ledger` **(preferred)**
- `codex-usage-archive`

### Package name and CLI command
- Python package: `codex_ledger`
- CLI command: `codex-ledger`

### License
Use **MIT** for v1 unless the owner explicitly chooses another license.

---

## 6. GitHub bootstrap procedure

If GitHub CLI auth is available, do this:

```bash
mkdir codex-ledger
cd codex-ledger
git init -b main

uv init --package .
rm -f main.py

mkdir -p src/codex_ledger tests docs schemas pricing scripts .github/workflows
touch src/codex_ledger/__init__.py

git add .
git commit -m "chore: initialize repository"

gh repo create <github-username>/codex-ledger --public --source=. --remote=origin --push
```

If GitHub CLI auth is not available:
1. initialize the repo locally
2. create the empty GitHub repo manually in the browser
3. add the remote
4. push immediately

```bash
git remote add origin git@github.com:<github-username>/codex-ledger.git
git push -u origin main
```

### GitHub settings to enable early
- Issues: on
- Releases: on
- Discussions: off initially
- Wiki: off
- Actions: on
- Dependabot/security alerts: on
- default branch: `main`

### After first successful CI run
Enable branch protection on `main`:
- require CI status checks before merge
- require linear history
- prevent force pushes

---

## 7. Repository structure

Use this layout:

```text
codex-ledger/
├─ .github/
│  └─ workflows/
│     ├─ ci.yml
│     └─ release.yml
├─ docs/
│  ├─ ARCHITECTURE.md
│  ├─ DATA_MODEL.md
│  ├─ REPORT_SCHEMA.md
│  ├─ PRIVACY.md
│  ├─ PRICING_PROVENANCE.md
│  ├─ FUTURE_WORK.md
│  ├─ SECURITY.md
│  ├─ CONTRIBUTING.md
│  └─ CHANGELOG.md
├─ pricing/
│  ├─ README.md
│  └─ rules/
├─ schemas/
│  └─ reports/
├─ scripts/
├─ src/
│  └─ codex_ledger/
│     ├─ cli/
│     ├─ domain/
│     ├─ ingest/
│     ├─ normalize/
│     ├─ providers/
│     │  └─ codex/
│     ├─ pricing/
│     ├─ reports/
│     ├─ render/
│     ├─ storage/
│     ├─ migrations/
│     └─ utils/
├─ tests/
│  ├─ fixtures/
│  ├─ unit/
│  ├─ integration/
│  └─ snapshots/
├─ AGENTS.md
├─ README.md
├─ LICENSE
├─ pyproject.toml
├─ .python-version
├─ .gitignore
└─ .editorconfig
```

### Notes
- Use `src/` layout.
- Keep docs in `docs/` rather than the root except for `README.md`, `LICENSE`, and `AGENTS.md`.
- Keep SQL migrations as plain files in `src/codex_ledger/migrations/`.
- Keep report JSON schemas in `schemas/reports/`.
- Do not put personal archive data in the repository.

---

## 8. External archive home

The project must store imported raw artifacts and generated reports outside app-owned directories.

### Default archive home
`~/AI-Usage-Archive/codex-ledger/`

### Override
Environment variable:
`CODEX_LEDGER_HOME`

### Archive home layout
```text
~/AI-Usage-Archive/codex-ledger/
├─ raw/
├─ ledger/
├─ pricing/
├─ reports/
└─ state/
```

### Meaning
- `raw/`: immutable copied source files
- `ledger/`: SQLite database
- `pricing/`: versioned imported pricing rule snapshots
- `reports/`: generated JSON, HTML, PNG
- `state/`: import manifests, cursors, hashes, repair logs

---

## 9. Architecture

Use a layered architecture with explicit boundaries.

### 9.1 Top-level modules
- `collector`: discovers input artifacts
- `normalizer`: turns provider-specific artifacts into canonical domain records
- `storage`: writes immutable raw files and canonical SQLite rows
- `pricing`: computes versioned cost estimates
- `reports`: generates aggregate and workspace report objects
- `render`: renders HTML and PNG from report objects
- `cli`: user-facing commands
- `providers`: provider-specific adapters

### 9.2 Top-level abstractions required now
These must exist now to avoid a future rewrite:

- `provider`: who generated the data (`codex`, later `claude`, `opencode`, etc.)
- `host`: where that provider ran (`standalone_cli`, later `zed`, `desktop_app`, `ide_extension`, etc.)
- `source_kind`: what was imported (`local_rollout_file`, `imported_json_report`, `stdout_json_capture`, `cloud_task_json`, `host_session_history`, `reconciliation_reference`)

### 9.3 Canonical truth model
Canonical truth is:
1. immutable raw imports
2. normalized canonical events in SQLite

Everything else is derived.

### 9.4 No ORM
Use the stdlib `sqlite3` module with explicit SQL, migrations, and repository helpers. Do not use an ORM.

### 9.5 SQLite mode
Set:
- `PRAGMA foreign_keys = ON`
- `PRAGMA journal_mode = WAL`
- use **STRICT** tables where appropriate

---

## 10. Canonical schema

The following tables are required.

### 10.1 `schema_migrations`
Tracks applied DB migrations.

### 10.2 `import_batches`
One row per import/sync operation.

Required fields:
- `batch_id`
- `started_at_utc`
- `completed_at_utc`
- `provider`
- `host`
- `source_kind`
- `importer_version`
- `manifest_json`

### 10.3 `raw_files`
One row per copied source artifact.

Required fields:
- `raw_file_id`
- `batch_id`
- `provider`
- `host`
- `source_kind`
- `original_path`
- `original_path_sha256`
- `sha256`
- `size_bytes`
- `stored_relpath`
- `copied_at_utc`

Unique constraint:
- `(provider, source_kind, sha256)`

### 10.4 `accounts`
Supports combined totals across multiple identities.

Required fields:
- `account_key`
- `display_name`
- `source_identity_json`
- `first_seen_at_utc`
- `last_seen_at_utc`

### 10.5 `sessions`
One row per logical Codex session.

Required fields:
- `session_id`
- `provider`
- `host`
- `account_key`
- `raw_session_id`
- `started_at_utc`
- `ended_at_utc`
- `session_meta_json`
- `source_first_seen_at_utc`
- `source_last_seen_at_utc`

### 10.6 `agent_runs`
One row per top-level or spawned agent/subagent execution lineage.

Required fields:
- `agent_run_id`
- `session_id`
- `parent_agent_run_id`
- `agent_name`
- `agent_role`
- `model_id`
- `started_at_utc`
- `ended_at_utc`
- `raw_metadata_json`

### 10.7 `workspaces`
Canonical resolved workspace identity.

Required fields:
- `workspace_key`
- `resolved_root_path`
- `resolved_root_path_sha256`
- `display_label`
- `redacted_label`
- `resolution_strategy`
- `first_seen_at_utc`
- `last_seen_at_utc`

### 10.8 `models`
Canonical model catalog entries observed in imported data.

Required fields:
- `model_id`
- `provider`
- `family`
- `supports_reasoning`
- `metadata_json`

### 10.9 `usage_events`
This is the most important table.

Required fields:
- `event_id`
- `batch_id`
- `raw_file_id`
- `session_id`
- `agent_run_id`
- `provider`
- `host`
- `source_kind`
- `event_ts_utc`
- `report_timezone`
- `local_date`
- `source_line`
- `turn_index`
- `event_kind`
- `raw_cwd`
- `workspace_key`
- `model_id`
- `service_tier`
- `reasoning_effort_recorded`
- `input_tokens`
- `cached_input_tokens`
- `output_tokens`
- `reasoning_output_tokens`
- `total_tokens`
- `dedupe_fingerprint`
- `raw_event_json`

Unique constraint:
- `dedupe_fingerprint`

### 10.10 `pricing_rule_sets`
Versioned rule definitions.

Required fields:
- `rule_set_id`
- `pricing_plane`
- `version`
- `effective_from`
- `effective_to`
- `source_url`
- `source_hash`
- `stability`
- `notes`

### 10.11 `cost_estimates`
Versioned computed estimates per event.

Required fields:
- `cost_id`
- `event_id`
- `rule_set_id`
- `pricing_plane`
- `currency`
- `amount`
- `confidence`
- `explanation_json`
- `computed_at_utc`

Unique constraint:
- `(event_id, rule_set_id)`

### 10.12 `report_runs`
Tracks report generation provenance.

Required fields:
- `report_run_id`
- `report_type`
- `period`
- `as_of_date`
- `timezone`
- `redaction_mode`
- `schema_version`
- `created_at_utc`
- `input_snapshot_json`

### 10.13 `report_artifacts`
One row per emitted artifact.

Required fields:
- `artifact_id`
- `report_run_id`
- `artifact_kind`
- `relpath`
- `sha256`
- `created_at_utc`

### 10.14 Derived views
Derived SQL views or report queries may exist for:
- daily usage by model
- daily usage by workspace
- daily usage by account
- daily cost by pricing plane

These are **derived only** and must not be treated as the canonical ledger.

---

## 11. Input discovery and source kinds

### v1 supported discovery
- `~/.codex/sessions/**`
- `~/.codex/archived_sessions/**`
- user-provided JSON backfill files
- user-provided machine-readable Codex JSON files

### Source kinds to support in the architecture now
- `local_rollout_file`
- `imported_json_report`
- `stdout_json_capture`
- `cloud_task_json`
- `host_session_history`
- `reconciliation_reference`

### Why this matters
Codex can produce machine-readable JSON in non-interactive mode and can run without persisting rollout files. Cloud task JSON and future host/editor session history sources also exist. If the architecture assumes only local transcript files, a major refactor will be required later.

---

## 12. Workspace attribution and privacy

### 12.1 Workspace signal priority
1. `turn_context.cwd`
2. `session_meta.cwd`
3. `unknown`

### 12.2 Workspace resolution
For every observed raw cwd, resolve:
- `raw_cwd`
- `resolved_root_path`
- `resolution_strategy`

Resolution strategies:
- `project_root_marker`
- `git_root`
- `raw_cwd`
- `unknown`

### 12.3 Project root detection
Use project root marker logic, not only raw cwd. The resolved root is the workspace identity used for aggregation.

### 12.4 Output redaction policy
Default output mode:
- do **not** expose absolute paths in JSON, HTML, CLI tables, or PNG
- use redacted or aliased workspace labels by default

Allow an explicit override:
- `--redaction-mode full|alias|redacted`
- default: `redacted`

### 12.5 Ledger retention policy
The ledger may keep:
- absolute path
- path hash
- derived label
- redacted label

Public artifacts must default to `redacted` unless the user explicitly opts into a less private mode.

---

## 13. Pricing and cost policy

### 13.1 Authoritative metric
The authoritative unit is **tokens**.

### 13.2 Stable v1 pricing output
Ship **USD estimates** in v1 as the stable public estimate, using explicit versioned pricing rule files.

### 13.3 Credits policy
Do **not** present ChatGPT credit estimation as a fully stable public feature in the first release.

Instead:
- architect for multiple pricing planes now
- store pricing rule sets and cost estimates generically
- either:
  - defer credit estimation to v1.1, or
  - keep it clearly marked experimental behind explicit `--plan` and `--rate-card` inputs

### 13.4 Pricing planes to support in architecture
- `api_usd`
- `chatgpt_credits_legacy`
- `chatgpt_credits_token_based`

### 13.5 Confidence labels
Every cost estimate must include confidence:
- `stable`
- `estimated`
- `experimental`

### 13.6 Pricing provenance
Every report that includes cost must record:
- rule set id
- rule set version
- pricing plane
- effective date window
- generation timestamp

---

## 14. Reports

### 14.1 Aggregate reports
Generate:
- daily JSON
- weekly JSON
- monthly JSON
- yearly JSON
- PNG heatmap from aggregate JSON

### 14.2 Workspace reports
Generate:
- JSON
- CLI table
- static HTML

### 14.3 Report JSON requirements
Every report JSON must include:
- `schema_version`
- `generated_at_utc`
- `generator_version`
- `filters`
- `timezone`
- `redaction_mode`
- `pricing`
- `data`

### 14.4 Aggregate report data
Include:
- totals by period
- totals by model
- totals by account
- totals by workspace count
- optional USD estimate
- top models for each period/day

### 14.5 Workspace report rows
Include:
- workspace label
- token totals
- USD estimate
- top model
- reasoning tokens
- session count
- agent run count
- first seen / last seen in selected period

### 14.6 Explainability commands
This is a strong v1 feature and should ship:

- `codex-ledger explain day --date YYYY-MM-DD`
- `codex-ledger explain workspace --workspace <workspace-key> --period ...`
- `codex-ledger explain model --model <model-id> --period ...`

The output must trace totals back to:
- sessions
- agent runs
- source artifacts
- model ids
- workspace attribution

This feature is part of the product position and is not optional.

---

## 15. CLI surface

Use grouped commands.

```text
codex-ledger sync local [--full-backfill]
codex-ledger import file --kind <kind> --input <path>
codex-ledger report aggregate --period day|week|month|year --as-of YYYY-MM-DD
codex-ledger report workspace --period day|week|month|year --as-of YYYY-MM-DD
codex-ledger render heatmap --report <json> --output <png>
codex-ledger render workspace-html --report <json> --output <html>
codex-ledger explain day --date YYYY-MM-DD
codex-ledger explain workspace --workspace <workspace-key> --period ...
codex-ledger explain model --model <model-id> --period ...
codex-ledger price recalc --rule-set <rule-set-id>
codex-ledger verify ledger
codex-ledger doctor
```

### CLI quality requirements
- clear `--help`
- sensible defaults
- machine-readable error messages where appropriate
- deterministic exit codes
- no hidden side effects

---

## 16. Tooling choices

### Package/build
- Python `>=3.12`
- `pyproject.toml`
- `src/` layout
- `uv` for local project management
- `hatchling` as the build backend
- publish installable CLI for `pipx`

### Runtime libraries
Keep dependencies modest and explicit.

Recommended:
- `typer` for CLI
- `rich` for terminal tables
- `jinja2` for static HTML
- `pillow` for deterministic PNG rendering
- `jsonschema` for report schema validation

### Do not use
- ORM
- heavyweight web framework
- frontend framework
- network dependency in test suite

### Quality
- `pytest`
- `ruff`
- `mypy`

---

## 17. Documentation files required before implementation is “done”

Create all of these:

- `README.md`
- `AGENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/DATA_MODEL.md`
- `docs/REPORT_SCHEMA.md`
- `docs/PRIVACY.md`
- `docs/PRICING_PROVENANCE.md`
- `docs/FUTURE_WORK.md`
- `docs/SECURITY.md`
- `docs/CONTRIBUTING.md`
- `docs/CHANGELOG.md`

### README must cover
- what the project is
- what problem it solves
- install
- quickstart
- privacy defaults
- examples
- data sources supported
- release status and scope

---

## 18. AGENTS.md requirements

Create an `AGENTS.md` file immediately. It must include rules like these:

```md
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
```

---

## 19. Testing strategy

The test suite must be stricter than the original proposal.

### Required test categories
1. **unit tests**
2. **integration tests**
3. **snapshot tests**
4. **migration tests**
5. **fixture sanitization tests**

### Required test cases
- import same raw file twice → no duplicate events
- import same logical session from active + archived locations → dedupe correctly
- malformed JSONL lines do not corrupt the ledger
- `turn_context.cwd` overrides session-level cwd
- unresolved cwd becomes `unknown`
- model ids that resemble paths are treated as model ids, not workspaces
- subagent lineage is preserved correctly
- rebuilding reports from same snapshot yields identical JSON
- repeated HTML rendering is stable
- repeated PNG rendering is stable
- timezone edge cases
- DST boundary cases
- leap day cases
- year rollover cases
- schema migration forward test
- fresh rebuild from raw imports equals incremental import result
- outputs redact paths by default
- `--redaction-mode full` exposes paths only when explicitly requested
- pricing rule regression tests
- doctor command verifies expected discovery and warns on missing local sources

### Fixture policy
- use only sanitized local fixtures
- add a scrubbing helper in `scripts/`
- never commit live personal data

---

## 20. CI and release

### CI on every push and PR
Run:
- Ruff check
- Ruff format check
- mypy
- pytest
- packaging build

### Release workflow
- GitHub release tag
- build wheel and sdist
- publish to PyPI using trusted publishing
- attach artifacts to GitHub release

### Release criteria for v0.1.0
Do not release until all are true:
- install works via `pipx`
- `codex-ledger --help` is clean
- local sync works on sample fixtures
- report JSON is schema-stamped
- HTML and PNG rendering are deterministic
- privacy defaults are documented and tested
- README quickstart succeeds on a clean machine

---

## 21. Good-match backlog features that are worth planning for now

These are not all required for day-one release, but the architecture should make them easy.

### 21.1 Privacy-safe share bundle
A command like:
`codex-ledger export share-bundle --redact default`

Purpose:
- create a support/debug/report bundle safe to share publicly
- redact paths, env hints, tokens, usernames, and sensitive strings by default
- include JSON plus a human-readable summary

This is a strong fit for the project and aligns with real user needs around safe transcript/report sharing.

### 21.2 Headless/CI ingestion
Import usage from machine-readable Codex JSON emitted by scripted runs. This is a very good fit because it extends the same ledger into automation and CI use cases.

### 21.3 Host-aware deduplication
When the same underlying Codex work later appears through editor/host history sources, dedupe or reconcile instead of double counting.

### 21.4 User-managed workspace alias map
Allow a simple config file or command to map redacted workspace hashes to stable human labels for personal local reports.

---

## 22. What not to do

Do **not** do any of the following:

- do not make `daily_*` tables the primary source of truth
- do not expose absolute paths in default outputs
- do not depend on undocumented app caches as the main data source
- do not ship a frontend stack in v1
- do not use placeholders, TODO-heavy scaffolds, or unimplemented commands in the first public release branch
- do not add credit calculations without explicit plan/rate-card handling
- do not hardcode user-specific paths into docs, tests, or code
- do not treat one provider as the permanent only provider in architecture

---

## 23. Recommended operating mode for the coding agent

### Primary surface
Use **Codex CLI** as the main implementation environment for this project.

Use the desktop app only as an optional secondary surface for quick browsing or ad hoc exploration. The primary build environment should be CLI because this project is local-file-heavy, terminal-oriented, and benefits from scripted, deterministic command execution.

### Recommended model
Use **`gpt-5.4`** as the default working model.

### Recommended reasoning effort
- **`high`** for:
  - architecture
  - schema design
  - migrations
  - parser implementation
  - testing
  - refactors
  - release review
- **`medium`** for:
  - straightforward implementation
  - docs
  - basic command wiring
- **`xhigh`** only for:
  - thorny bugs
  - unexpected parsing failures
  - final design review on difficult changes

Do **not** use `xhigh` as the default. It is too expensive and too slow for everyday implementation.

### Model selection rule
If choosing between `gpt-5.4` and `gpt-5.3-codex`, prefer `gpt-5.4` as the default. Use `gpt-5.3-codex` only if a specific coding-only benchmark or behavior suggests it is needed for a narrow task.

---

## 24. Implementation order

Codex should implement in this order and stop after each phase with a status summary.

### Phase 0 — Scaffold and guardrails
Deliver:
- repo skeleton
- `pyproject.toml`
- `README.md` stub
- `AGENTS.md`
- CI workflow
- lint/type/test harness
- migration runner skeleton
- `.gitignore`, `.editorconfig`, `.python-version`

### Phase 1 — Canonical storage and imports
Deliver:
- archive home resolution
- raw file copier
- SQLite schema + migrations
- local session discovery
- imported JSON ingestion
- event dedupe
- basic `doctor`
- tests

### Phase 2 — Workspace and lineage
Deliver:
- workspace resolver
- project-root resolution
- redaction/alias modes
- agent/subagent lineage support
- tests

### Phase 3 — Pricing layer
Deliver:
- pricing rule loader
- stable USD estimation
- provenance storage
- `price recalc`
- tests

### Phase 4 — Reports and explainability
Deliver:
- aggregate JSON report
- workspace JSON report
- CLI tables
- `explain day/workspace/model`
- tests and snapshots

### Phase 5 — Rendering and release
Deliver:
- PNG heatmap
- static HTML workspace report
- release workflow
- docs completion
- v0.1.0 readiness checklist

---

## 25. Immediate next action for Codex

Start with **Phase 0** only.

The first task is:

1. initialize the repository locally
2. create the file structure in this brief
3. add `pyproject.toml` with package metadata
4. add `AGENTS.md`
5. add docs stubs
6. add CI
7. add a migration runner skeleton
8. add a minimal `codex-ledger --help`
9. add lint/type/test commands
10. run the test and lint suite
11. commit as:
   - `chore: scaffold codex-ledger v1`
12. if GitHub auth is available, create and push the public repo
13. stop and present a concise status summary before Phase 1

Do **not** jump ahead to pricing, reports, or HTML in the first pass.

---

## 26. Official upstream references that may inform implementation

Only use these as upstream documentation references. Do not mirror their code.

- Codex CLI: https://developers.openai.com/codex/cli/
- Codex CLI reference: https://developers.openai.com/codex/cli/reference
- AGENTS.md guidance: https://developers.openai.com/codex/guides/agents-md
- Codex config reference: https://developers.openai.com/codex/config-reference
- Codex models: https://developers.openai.com/codex/models
- Codex prompting guide: https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide
- Codex rate card: https://help.openai.com/en/articles/20001106-codex-rate-card
- Python CLI packaging: https://packaging.python.org/en/latest/guides/creating-command-line-tools/
- pipx install guidance: https://packaging.python.org/en/latest/guides/installing-stand-alone-command-line-tools/
- uv project workflow: https://docs.astral.sh/uv/guides/projects/
- uv build/publish: https://docs.astral.sh/uv/guides/package/
- GitHub OIDC trusted publishing for PyPI: https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-pypi
- GitHub Python build/test workflow examples: https://docs.github.com/en/actions/tutorials/build-and-test-code/python
- SQLite docs: https://sqlite.org/docs.html
- SQLite pragma reference: https://sqlite.org/pragma.html
- Zed stable release notes (future host/session-history relevance): https://zed.dev/releases/stable/0.225.9
