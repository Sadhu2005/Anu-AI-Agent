-- ANU Brain migration 0002 (V0.6.0). Additive only.
-- Applied when missing by anu-safe-mutation-v0.2.py ensure_state() (step 2)
-- and by ensure_task_columns() in anu-recovery-v0.1.py and the Task Engine
-- (step 4); recorded here so the schema is reproducible from migrations alone.

-- Step 2: write-ahead transaction record and idempotency key.
ALTER TABLE mutation_transactions ADD COLUMN idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mutation_transactions_committed_key
    ON mutation_transactions(idempotency_key)
    WHERE status = 'COMMITTED' AND idempotency_key IS NOT NULL;

-- Step 4: retry classification and scheduled retry of interrupted work.
ALTER TABLE tasks ADD COLUMN failure_class TEXT;
ALTER TABLE tasks ADD COLUMN retry_after TEXT;
