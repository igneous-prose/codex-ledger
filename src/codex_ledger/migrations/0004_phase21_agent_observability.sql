ALTER TABLE agent_runs ADD COLUMN agent_kind TEXT;
ALTER TABLE agent_runs ADD COLUMN requested_model_id TEXT;
ALTER TABLE agent_runs ADD COLUMN lineage_status TEXT;
ALTER TABLE agent_runs ADD COLUMN lineage_confidence TEXT;
ALTER TABLE agent_runs ADD COLUMN unresolved_reason TEXT;

UPDATE agent_runs
SET agent_kind = CASE
        WHEN lineage_key = 'root' AND raw_parent_agent_run_id IS NULL THEN 'root'
        ELSE 'subagent'
    END,
    requested_model_id = COALESCE(requested_model_id, model_id),
    lineage_status = CASE
        WHEN lineage_key = 'root' AND raw_parent_agent_run_id IS NULL THEN 'root_placeholder'
        WHEN raw_parent_agent_run_id IS NOT NULL AND parent_agent_run_key IS NOT NULL THEN 'resolved'
        WHEN raw_parent_agent_run_id IS NOT NULL THEN 'child_only_orphaned'
        ELSE 'resolved'
    END,
    lineage_confidence = CASE
        WHEN lineage_key = 'root' AND raw_parent_agent_run_id IS NULL THEN 'placeholder'
        WHEN raw_parent_agent_run_id IS NOT NULL AND parent_agent_run_key IS NOT NULL
            THEN 'session_metadata_only'
        WHEN raw_parent_agent_run_id IS NOT NULL THEN 'session_metadata_only'
        ELSE 'placeholder'
    END,
    unresolved_reason = CASE
        WHEN raw_parent_agent_run_id IS NOT NULL AND parent_agent_run_key IS NULL
            THEN 'parent_session_missing'
        ELSE NULL
    END;

CREATE INDEX idx_agent_runs_lineage_status ON agent_runs(lineage_status);
CREATE INDEX idx_agent_runs_agent_kind ON agent_runs(agent_kind);
