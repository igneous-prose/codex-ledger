# Privacy

Codex Ledger keeps sensitive provenance local to the archive home.

Phase 2 stores both internal and outward-facing workspace fields:

- internal: `raw_cwd`, `resolved_root_path`, source file paths, and content hashes
- outward-facing defaults: `display_label`, `redacted_display_label`, and `workspace_key`

The implemented workspace label modes are:

- `redacted`: default mode for outward-facing output
- `alias`: uses a locally stored alias when one exists, otherwise falls back to the redacted label
- `full`: returns the resolved root path and is opt-in only

Absolute paths remain ledger-internal by default. The current implementation does not
emit them in default CLI output, fixtures, or docs examples.
