# Pricing Rule Files

Each file in this directory is a development mirror of a versioned pricing rule snapshot
packaged under `src/codex_ledger/pricing/rules_data/`.

Current schema:

- `pricing-rule-set-v1`

Each file should define:

- rule-set identity and pricing plane
- effective date window
- token-field mapping
- provider/model match rules
- provenance metadata
- confidence and stability labels

Rules are loaded offline from the packaged data set. Repository mirrors are checked for
byte-for-byte equality; mismatches or unexpected repo-only files fail fast so local edits do
not silently alter the reference pricing baseline. Invalid or overlapping rule windows also
fail fast.
