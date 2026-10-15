-- ANU Brain schema baseline.
-- Captured read-only from /srv/anu/brain/anu-brain.db on 15 Oct 2026
-- (sqlite_master, running tag v0.5.9). DDL is verbatim; only the order
-- (tables before indexes) differs. sqlite_sequence is maintained by SQLite.
-- Do not edit: schema changes go in later numbered migrations.

CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    repository TEXT NOT NULL,
    workspace TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER,
    parent_task_id INTEGER,
    task_key TEXT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 5,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(goal_id) REFERENCES goals(id),
    FOREIGN KEY(parent_task_id) REFERENCES tasks(id)
);

CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(task_id, depends_on_task_id),

            CHECK(task_id != depends_on_task_id),

            FOREIGN KEY(task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE,

            FOREIGN KEY(depends_on_task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE
        );

CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE
        );

CREATE TABLE agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    message TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
, task_id INTEGER, goal_id INTEGER, parent_task_id INTEGER, run_id TEXT, attempt INTEGER);

CREATE TABLE agent_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    action_type TEXT NOT NULL,
    command TEXT,
    status TEXT NOT NULL,
    output TEXT,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    action TEXT NOT NULL,
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    phase TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE mutation_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                file_path TEXT NOT NULL,
                operation TEXT NOT NULL,
                status TEXT NOT NULL,
                baseline_ok INTEGER,
                mutation_test_ok INTEGER,
                rollback_attempted INTEGER,
                rollback_verified INTEGER,
                checkpoint_path TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            , transaction_id TEXT, task_id INTEGER, files_changed INTEGER, baseline_sha256 TEXT, rollback_sha256 TEXT, integrity_verified INTEGER, completed_at TEXT);

CREATE TABLE test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    test_name TEXT,
    status TEXT NOT NULL,
    output TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    observation_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    error_type TEXT,
    message TEXT,
    resolution TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE resource_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cpu_percent REAL,
    memory_mb REAL,
    temperature_c REAL,
    disk_free_gb REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE research (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    url TEXT,
    title TEXT,
    summary TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    commit_hash TEXT,
    branch TEXT,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE pull_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    pr_number INTEGER,
    url TEXT,
    status TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE INDEX idx_agent_events_run_id
    ON agent_events(run_id, id DESC);

CREATE INDEX idx_agent_events_task_id
    ON agent_events(task_id, id DESC);

CREATE UNIQUE INDEX idx_mutation_transactions_transaction_id ON mutation_transactions(transaction_id);

CREATE INDEX idx_task_dependencies_prerequisite
        ON task_dependencies(depends_on_task_id);

CREATE INDEX idx_task_dependencies_task
        ON task_dependencies(task_id);
