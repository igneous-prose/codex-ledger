# Report Schemas

Phase 4 adds lightweight JSON Schema documents for the stable report and explain payloads.

Current schema files:

- `aggregate-report-v1.schema.json`
- `workspace-report-v1.schema.json`
- `agent-diagnostics-v1.schema.json`
- `explain-report-v1.schema.json`

The schemas intentionally validate the stable top-level contract and major blocks rather
than every nested analytic detail.
