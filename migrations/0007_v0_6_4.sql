-- ANU Brain migration 0007 (V0.6.4). Additive only.
-- Project intelligence model snapshot per registered project.

CREATE TABLE IF NOT EXISTS project_models (
    project_id INTEGER PRIMARY KEY,
    model_json TEXT NOT NULL,
    refreshed_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_projects_status
    ON projects(status);
