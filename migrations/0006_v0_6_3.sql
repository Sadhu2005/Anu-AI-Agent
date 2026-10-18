-- ANU Brain migration 0006 (V0.6.3). Additive only.
-- Structured agent memories with provenance to observations.
-- Raw captures remain in existing observations table.

CREATE TABLE IF NOT EXISTS agent_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER NOT NULL,
    task_id INTEGER,
    goal_id INTEGER,
    project_key TEXT,
    capability TEXT,
    failure_class TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(observation_id) REFERENCES observations(id),
    FOREIGN KEY(task_id) REFERENCES tasks(id),
    FOREIGN KEY(goal_id) REFERENCES goals(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_memories_project
    ON agent_memories(project_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_memories_capability
    ON agent_memories(capability, failure_class);

CREATE INDEX IF NOT EXISTS idx_agent_memories_expires
    ON agent_memories(expires_at);

CREATE INDEX IF NOT EXISTS idx_observations_task
    ON observations(task_id, id DESC);
