CREATE TABLE pricing_rule_sets (
    rule_set_id TEXT PRIMARY KEY,
    pricing_plane TEXT NOT NULL,
    version TEXT NOT NULL,
    effective_from_utc TEXT,
    effective_to_utc TEXT,
    currency TEXT NOT NULL,
    stability TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_path TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    loaded_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE cost_estimates (
    cost_estimate_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES usage_events(event_id),
    rule_set_id TEXT NOT NULL REFERENCES pricing_rule_sets(rule_set_id),
    pricing_plane TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount REAL,
    confidence TEXT NOT NULL,
    estimate_status TEXT NOT NULL,
    explanation_json TEXT NOT NULL,
    computed_at_utc TEXT NOT NULL,
    UNIQUE(event_id, rule_set_id, pricing_plane)
) STRICT;

CREATE INDEX idx_cost_estimates_rule_set_id ON cost_estimates(rule_set_id);
CREATE INDEX idx_cost_estimates_event_id ON cost_estimates(event_id);
CREATE INDEX idx_cost_estimates_status ON cost_estimates(estimate_status);
