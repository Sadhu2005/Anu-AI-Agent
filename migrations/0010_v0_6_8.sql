-- ANU Brain migration 0010 (V0.6.8). Additive only.
-- Durable wake conditions evaluated after restart.

CREATE TABLE IF NOT EXISTS wake_conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    goal_id INTEGER,
    wake_kind TEXT NOT NULL,
    wake_at TEXT,
    payload_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    satisfied_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(id),
    FOREIGN KEY(goal_id) REFERENCES goals(id)
);

CREATE INDEX IF NOT EXISTS idx_wake_pending
    ON wake_conditions(status, wake_kind);

CREATE INDEX IF NOT EXISTS idx_wake_task
    ON wake_conditions(task_id, status);
