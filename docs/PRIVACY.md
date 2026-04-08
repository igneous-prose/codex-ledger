# Privacy

Codex Ledger keeps sensitive provenance local to the archive home.

The ledger stores both internal and outward-facing workspace fields:

- internal: `raw_cwd`, `resolved_root_path`, source file paths, and content hashes
- outward-facing defaults: `display_label`, `redacted_display_label`, and `workspace_key`

The implemented workspace label modes are:

- `redacted`: default mode for outward-facing output
- `alias`: uses a locally stored alias when one exists, otherwise falls back to the redacted label
- `full`: returns the resolved root path and is opt-in only

Absolute paths remain ledger-internal by default for outward-facing artifacts.
Default text output for `sync`, `import codex-json`, `doctor`, and `migrate` avoids
absolute local paths unless you opt in with `--show-full-paths`. JSON diagnostics can
still carry canonical local paths when machine-readable output is the point of the
command.

Aggregate, workspace, agent, explain, and render surfaces keep the same privacy defaults:

- redacted by default
- alias only when explicitly requested
- full paths only when explicitly requested

Explain commands expose canonical IDs, token totals, lineage status, requested and
observed models, stored raw artifact relpaths, and priced versus unpriced coverage
without defaulting to absolute workspace paths.

Phase 5 delivery artifacts keep the same default:

- saved report JSON includes the selected `redaction_mode`
- rendered HTML reflects the selected redaction mode from the source workspace report
- render sidecars record the chosen redaction mode and source-report hash
- render sidecars use report names and hashes rather than absolute source-report paths by default
