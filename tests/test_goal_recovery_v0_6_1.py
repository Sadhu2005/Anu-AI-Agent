#!/usr/bin/env python3
"""V0.6.1 step 6: block or replan a goal after task-graph failure."""

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
    mod = load(REPO / "bin" / "anu-goal-recovery-v0.1.py", "goal_recovery_v061")
    mod.BASE = REPO
    return mod


def load_planner():
    mod = load(REPO / "bin" / "anu-template-planner-v0.1.py", "planner_recovery_v061")
    mod.BASE = REPO
    return mod


def load_aggregator():
    mod = load(REPO / "bin" / "anu-goal-aggregator-v0.1.py", "agg_recovery_v061")
    mod.BASE = REPO
    return mod


def make_expanded_db():
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
        "INSERT INTO goals(description, status) VALUES ('Improve', 'pending')"
    )
    db.commit()
    planner = load_planner()
    expansion = planner.expand_goal(
        db,
        1,
        project="sandbox-test-project",
        plan_id="plan-rec-1",
    )
    return path, db, expansion


def test_block_goal_retires_open_tasks():
    recovery = load_recovery()
    path, db, expansion = make_expanded_db()
    try:
        first_task = expansion["tasks"][0]["task_id"]
        # Simulate one completed, one failed mid-graph.
        db.execute(
            "UPDATE tasks SET status='completed' WHERE id=?",
            (first_task,),
        )
        fail_id = expansion["tasks"][1]["task_id"]
        db.execute(
            "UPDATE tasks SET status='failed' WHERE id=?",
            (fail_id,),
        )
        db.commit()

        result = recovery.handle_task_failure(
            db,
            fail_id,
            "mutation blocked integrity",
            policy="block",
        )
        assert result["policy"] == "block"
        assert result["goal"]["status"] == "blocked"

        rows = db.execute(
            "SELECT id, status FROM tasks WHERE goal_id=1 ORDER BY id"
        ).fetchall()
        by_id = {r["id"]: r["status"] for r in rows}
        assert by_id[first_task] == "completed"
        assert by_id[fail_id] == "blocked"
        # Remaining open tasks retired.
        for task in expansion["tasks"][2:]:
            assert by_id[task["task_id"]] == "blocked"

        events = [
            r["event_type"]
            for r in db.execute(
                "SELECT event_type FROM agent_events WHERE goal_id=1 "
                "AND event_type='GOAL_BLOCKED'"
            ).fetchall()
        ]
        assert events == ["GOAL_BLOCKED"]

        agg = load_aggregator()
        synced = agg.sync_goal_status(db, 1)
        assert synced["goal"]["status"] == "blocked"
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS block_goal retires open tasks and records reason")


def test_replan_supersedes_and_expands():
    recovery = load_recovery()
    path, db, expansion = make_expanded_db()
    try:
        fail_id = expansion["tasks"][2]["task_id"]
        db.execute(
            "UPDATE tasks SET status='blocked', failure_class='code_defect' "
            "WHERE id=?",
            (fail_id,),
        )
        db.commit()

        result = recovery.handle_goal_failure(
            db,
            1,
            "step apply failed; retry with fresh plan",
            policy="replan",
            task_id=fail_id,
            plan_id="plan-rec-2",
        )
        assert result["policy"] == "replan"
        assert result["goal"]["status"] == "active"
        assert result["expansion"]["plan"]["plan_id"] == "plan-rec-2"
        assert result["expansion"]["plan"]["version"] == 2
        # V0.8.2+: default template is multi_stage_goal (5 stages).
        assert len(result["expansion"]["tasks"]) == 5

        plans = load(
            REPO / "bin" / "anu-goal-plan-service-v0.1.py",
            "plans_rec_test",
        )
        plans.MODEL_MODULE = REPO / "bin" / "anu-agent-model-v0.1.py"
        old = plans.get_plan(db, "plan-rec-1")
        assert old["status"] == "superseded"

        # Old open tasks retired; new pending graph exists.
        pending = db.execute(
            "SELECT COUNT(*) AS n FROM tasks "
            "WHERE goal_id=1 AND status='pending'"
        ).fetchone()["n"]
        assert pending == 5

        events = [
            r["event_type"]
            for r in db.execute(
                "SELECT event_type FROM agent_events WHERE goal_id=1 "
                "AND event_type='GOAL_REPLANNED'"
            ).fetchall()
        ]
        assert events == ["GOAL_REPLANNED"]
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS replan supersedes plan and expands new task graph")


if __name__ == "__main__":
    print("===== ANU V0.6.1 GOAL RECOVERY REGRESSION =====")
    test_block_goal_retires_open_tasks()
    test_replan_supersedes_and_expands()
    print("GOAL_RECOVERY_TEST_COMPLETE")
