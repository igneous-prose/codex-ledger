# Codex Ledger Threat Model Review

## Status
- Refreshed against branch `codex/fix-security-findings`.
- Validated with `tests/unit/test_importer.py`, `tests/unit/test_delivery.py`, and `tests/integration/test_cli.py` on the current branch before this update.
- This document is an internal AppSec review plus a remediation backlog for remaining local-first hardening work.

## Current Security Posture
- Codex Ledger remains a local-first Python CLI that ingests local Codex artifacts, archives raw inputs, normalizes them into SQLite, applies offline pricing rules, and emits deterministic reports plus static render artifacts.
- There is still no network service, authentication layer, or multi-tenant boundary in the runtime. The dominant threats remain local integrity abuse, privacy leakage in shared outputs, and operator-triggered denial of service from attacker-supplied local files.

## Updated Controls
- Raw archive ingestion now rejects oversized inputs before hashing or copying and refuses symlinked archive roots, symlinked archive ancestors, and symlinked archive targets. Evidence: `src/codex_ledger/storage/archive.py`.
- Imported rollout/report parsing is bounded to 64 MiB per file before `read_text()` and returns structured parse failures for malformed JSON, malformed JSONL, unsupported shapes, and malformed Unicode. Evidence: `src/codex_ledger/providers/codex/parser.py`.
- Saved report artifacts are schema-validated on load and capped at 16 MiB for render input. Renderers only accept known schema versions. Evidence: `src/codex_ledger/reports/schema.py`, `src/codex_ledger/render/service.py`.
- Human-readable CLI output escapes control characters to reduce terminal injection risk from attacker-controlled database or artifact content. Evidence: `src/codex_ledger/utils/terminal.py`.
- Workspace and report views remain redacted by default, with alias or full-path disclosure only when explicitly selected. Evidence: `src/codex_ledger/normalize/privacy.py`, `src/codex_ledger/reports/common.py`, `src/codex_ledger/cli/main.py`.

## Corrected Threat Calibration
- The previous “archive symlink overwrite” story is stale for raw archive ingestion. The raw archive path now has direct anti-symlink enforcement.
- Ingestion and render denial of service should now be described as bounded per-file resource consumption for `sync`, `import`, and `render`, not unbounded whole-file parsing.
- Default-output privacy is not universal in the pre-refresh codebase because `doctor --json` exposed canonical local paths even though text output was privacy-safe by default. That gap is part of the current remediation backlog.

## Remaining Risks
1. Unbounded reconcile input parsing
   - `reconcile reference` accepted a local JSON file without a size cap, so a crafted large file could still drive memory-heavy local parsing.
   - Evidence: `src/codex_ledger/reconcile/service.py`.

2. Operator-selected output path abuse
   - Report JSON, rendered HTML, PNG, and provenance sidecar outputs were written to operator-selected paths without the same no-follow protections used by raw archive writes.
   - This is a local integrity risk, not a raw-ingestion bypass.
   - Evidence: `src/codex_ledger/reports/artifacts.py`, `src/codex_ledger/render/service.py`.

3. Pricing rule integrity remains high impact
   - Offline pricing rules still determine reference USD totals. The runtime now treats packaged rule data as authoritative and rejects drifted repo mirrors, which blocks silent local edits to `pricing/rules/*.json`.
   - Residual risk remains for package-level tampering or a compromised local install because there is still no external signature or trust anchor beyond the shipped bytes.
   - Evidence: `src/codex_ledger/pricing/rules.py`, `pricing/rules/*.json`.

4. SQLite and raw archive tampering remain local integrity risks
   - A local attacker who can edit the ledger or archive can still poison provenance, labels, or reconciliation results. `verify` detects internal inconsistencies but is not an anti-tamper system.
   - Evidence: `src/codex_ledger/storage/repository.py`, `src/codex_ledger/verify/service.py`.

## Revised Attacker Stories
1. Malicious local artifact DoS
   - Attacker places malformed or oversized rollout/report inputs in discovered or operator-selected locations.
   - Current effect is limited to bounded parse failure or per-file import rejection for `sync`, `import`, and `render`.

2. Local pricing rule tampering
   - Attacker edits offline rule JSON to shift pricing coverage or dollar totals.
   - Integrity impact remains high because reports are intended to be audit artifacts.

3. Output destination symlink abuse
   - Attacker pre-places symlinks in an operator-selected output path so report or render commands overwrite unintended local files.
   - This remains plausible until output writers adopt the same no-follow behavior as archive writes.

4. Reconcile input exhaustion
   - Attacker supplies a very large reference JSON to `reconcile`.
   - This remains the primary unbounded local parse surface until capped.

5. Local terminal confusion
   - Attacker tampers with SQLite values to include escape sequences or misleading labels.
   - Human-readable CLI output now escapes these values, so the residual risk is lower and mostly limited to raw JSON consumers.

## Priority Backlog
- Medium: consider integrity attestation for pricing rules if the tool will be used for stronger audit claims across installation boundaries.
- Low: consider documenting that JSON-mode diagnostics may still require operator care when shared externally.

## Review Focus Paths
- `src/codex_ledger/storage/archive.py`: raw archive copy, size limits, and anti-symlink enforcement.
- `src/codex_ledger/reconcile/service.py`: remaining unbounded local JSON ingest.
- `src/codex_ledger/reports/artifacts.py`: deterministic report writes to operator-selected paths.
- `src/codex_ledger/render/service.py`: render output and provenance sidecar writes.
- `src/codex_ledger/cli/main.py`: privacy defaults and CLI exposure surface.
- `src/codex_ledger/pricing/rules.py`: integrity-critical rule loading and validation.

## Acceptance Notes
- After the current hardening work lands, this review should no longer describe raw archive symlink overwrite or terminal escape injection as open findings.
- Pricing-rule integrity should remain called out as a valid risk unless the project adds a stronger trust model for rule files.
