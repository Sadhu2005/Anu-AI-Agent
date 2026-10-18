-- ANU Brain migration 0005: track applied SQL migrations.
-- Applied by bin/anu-migrate-v0.1.py (idempotent).

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
