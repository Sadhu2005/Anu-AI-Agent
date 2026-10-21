-- V0.9.0 autonomy foundation: objective, checklist, control, runs, fingerprints.

ALTER TABLE goals ADD COLUMN objective TEXT;
ALTER TABLE goals ADD COLUMN acceptance_criteria TEXT;
ALTER TABLE goals ADD COLUMN autonomous INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS goal_checklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL,
    item TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    evidence TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS agent_control (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    goal_id INTEGER,
    state TEXT NOT NULL DEFAULT 'run',
    reason TEXT,
    by_who TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scope, goal_id)
);

CREATE TABLE IF NOT EXISTS autonomy_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    iterations INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds REAL NOT NULL DEFAULT 0,
    stop_reason TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS failure_fingerprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER,
    task_id INTEGER,
    fingerprint TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_message TEXT,
    UNIQUE(goal_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS provider_health (
    provider TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'unknown',
    cooldown_until TEXT,
    last_error TEXT,
    error_class TEXT,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS provider_usage (
    provider TEXT NOT NULL,
    day TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(provider, day)
);

CREATE TABLE IF NOT EXISTS llm_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER,
    task_id INTEGER,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    provider TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS autonomy_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    branch_head TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_goal_checklist_goal ON goal_checklist(goal_id);
CREATE INDEX IF NOT EXISTS idx_llm_context_goal ON llm_context(goal_id);
CREATE INDEX IF NOT EXISTS idx_failure_fp_goal ON failure_fingerprints(goal_id);
CREATE INDEX IF NOT EXISTS idx_autonomy_runs_goal ON autonomy_runs(goal_id);
