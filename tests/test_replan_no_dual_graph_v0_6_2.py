#!/usr/bin/env python3
"""F2: replan must retire old graph; scheduler must not wake superseded tasks."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_recovery():
    mod = load(REPO / "bin" / "anu-goal-recovery-v0.1.py", "recovery_dual_v062")
    mod.BASE = REPO
    return mod


def load_scheduler():
    return load(REPO / "bin" / "anu-task-scheduler-v0.1.py", "sched_dual_v062")


def load_goal_service():
    return load(REPO / "bin" / "anu-goal-service-v0.1.py", "goal_dual_v062")


def load_plan_service():
    mod = load(REPO / "bin" / "anu-goal-plan-service-v0.1.py", "plan_dual_v062")
    mod.MODEL_MODULE = REPO / "bin" / "anu-agent-model-v0.1.py"
    return mod


def make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            failure_class TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(goal_id) REFERENCES goals(id)
        );
        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, depends_on_task_id),
            CHECK(task_id != depends_on_task_id),
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(depends_on_task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            task_id INTEGER,
            goal_id INTEGER,
            parent_task_id INTEGER,
            run_id TEXT,
            attempt INTEGER
        );
        """
    )
    db.execute(
        "INSERT INTO goals(description, status) VALUES ('dual graph test', 'active')"
    )
    db.commit()
    return path, db


def seed_old_plan_and_two_tasks(db):
    ps = load_plan_service()
    ps.migrate(db)
    ps.save_plan(
        db,
        1,
        {
            "plan_id": "plan-old",
            "goal_id": "1",
            "actions": [
                {
                    "action_id": "old-a1",
                    "goal_id": "1",
                    "kind": "TASK",
                    "description": "old step 1",
                    "parameters": {"template": "improve_sandbox"},
                }
            ],
        },
    )
    old_ids = []
    for idx, title in enumerate(("old pending one", "old pending two"), start=1):
        cur = db.execute(
            """INSERT INTO tasks(goal_id, title, status, priority)
               VALUES (1, ?, 'pending', ?)""",
            (title, 10 - idx),
        )
        task_id = cur.lastrowid
        old_ids.append(task_id)
        db.execute(
            """INSERT INTO task_execution_specs(
                   task_id, project, changes_file, operation
               ) VALUES (?, 'sandbox-test-project', 'changes.json', 'mutate')""",
            (task_id,),
        )
    db.commit()
    return old_ids


def test_replan_scheduler_ignores_superseded_tasks():
    recovery = load_recovery()
    scheduler = load_scheduler()
    goals = load_goal_service()
    path, db = make_db()
    try:
        old_ids = seed_old_plan_and_two_tasks(db)
        assert goals.get_goal(db, 1)["status"] == "active"

        result = recovery.replan_goal(
            db,
            1,
            "refresh graph after partial failure",
            project="sandbox-test-project",
            plan_id="plan-new",
        )
        assert result["policy"] == "replan"

        for task_id in old_ids:
            row = db.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            assert row["status"] == "superseded"

        ready_ids = {
            c["task_id"] for c in scheduler.discover_ready_tasks(db)
        }
        for task_id in old_ids:
            assert task_id not in ready_ids

        pending_ready = db.execute(
            "SELECT COUNT(*) AS n FROM tasks "
            "WHERE goal_id = 1 AND status = 'pending'"
        ).fetchone()["n"]
        assert pending_ready >= 1
        assert ready_ids
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS replan retires old tasks; scheduler skips superseded")


if __name__ == "__main__":
    print("===== ANU F2 REPLAN NO DUAL GRAPH REGRESSION =====")
    test_replan_scheduler_ignores_superseded_tasks()
    print("REPLAN_NO_DUAL_GRAPH_TEST_COMPLETE")
