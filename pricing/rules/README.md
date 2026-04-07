# Pricing Rule Files

Each file in this directory is a versioned pricing rule snapshot.

Current schema:

- `pricing-rule-set-v1`

Each file should define:

- rule-set identity and pricing plane
- effective date window
- token-field mapping
- provider/model match rules
- provenance metadata
- confidence and stability labels

Rules are loaded offline from the local repository. They are validated before use, and
invalid or overlapping rule windows fail fast.
