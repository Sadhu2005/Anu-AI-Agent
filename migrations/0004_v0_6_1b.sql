-- ANU Brain migration 0004 (V0.6.1b / review F5, F9).
-- Additive only. Also applied by anu-goal-plan-service migrate()
-- and anu-db helpers where present.

-- F5: at most one active plan per goal.
CREATE UNIQUE INDEX IF NOT EXISTS idx_goal_plans_one_active
    ON goal_plans(goal_id)
    WHERE status = 'active';

-- F9: task_key uniqueness (NULL keys allowed multiple times in SQLite UNIQUE).
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_task_key
    ON tasks(task_key)
    WHERE task_key IS NOT NULL;
