-- ANU Brain migration 0008 (V0.6.5). Additive only.
-- Formal verification records: ACCEPT | RETRY | ROLLBACK per task attempt.

CREATE TABLE IF NOT EXISTS verification_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    goal_id INTEGER,
    attempt INTEGER NOT NULL,
    run_id TEXT,
    transaction_id TEXT,
    decision TEXT NOT NULL,
    compile_ok INTEGER,
    tests_ok INTEGER,
    intended_ok INTEGER,
    unintended_ok INTEGER,
    checkpoint_ok INTEGER,
    rollback_ok INTEGER,
    goal_ok INTEGER,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id),
    FOREIGN KEY(goal_id) REFERENCES goals(id)
);

CREATE INDEX IF NOT EXISTS idx_verification_task_attempt
    ON verification_records(task_id, attempt);

CREATE INDEX IF NOT EXISTS idx_verification_goal
    ON verification_records(goal_id);
