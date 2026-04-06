ALTER TABLE workspaces ADD COLUMN raw_cwd TEXT;

UPDATE workspaces
SET raw_cwd = resolved_root_path
WHERE raw_cwd IS NULL;

CREATE TABLE workspace_aliases (
    workspace_key TEXT PRIMARY KEY REFERENCES workspaces(workspace_key),
    alias_label TEXT NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
) STRICT;

CREATE INDEX idx_workspace_aliases_alias_label ON workspace_aliases(alias_label);
