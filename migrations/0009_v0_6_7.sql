-- ANU Brain migration 0009 (V0.6.7). Additive only.
-- Enrich approvals for human-in-the-loop (risk, expiry, decision audit).

ALTER TABLE approvals ADD COLUMN reason TEXT;
ALTER TABLE approvals ADD COLUMN risk TEXT;
ALTER TABLE approvals ADD COLUMN proposed_change TEXT;
ALTER TABLE approvals ADD COLUMN expires_at TEXT;
ALTER TABLE approvals ADD COLUMN decided_by TEXT;
ALTER TABLE approvals ADD COLUMN decided_at TEXT;
ALTER TABLE approvals ADD COLUMN decision_note TEXT;
ALTER TABLE approvals ADD COLUMN goal_id INTEGER;
ALTER TABLE approvals ADD COLUMN classification TEXT;

CREATE INDEX IF NOT EXISTS idx_approvals_task_status
    ON approvals(task_id, status);

CREATE INDEX IF NOT EXISTS idx_approvals_expires
    ON approvals(expires_at);
