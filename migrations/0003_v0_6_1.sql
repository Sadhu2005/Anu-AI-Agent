-- ANU Brain migration 0003 (V0.6.1). Additive only.
-- Versioned plan artefacts for Goal → Plan → Task Graph.
-- Applied idempotently by anu-goal-plan-service-v0.1.py migrate().

CREATE TABLE IF NOT EXISTS goal_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL,
    plan_id TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    body_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(goal_id, version),
    FOREIGN KEY(goal_id) REFERENCES goals(id)
);

CREATE INDEX IF NOT EXISTS idx_goal_plans_goal_status
    ON goal_plans(goal_id, status);
