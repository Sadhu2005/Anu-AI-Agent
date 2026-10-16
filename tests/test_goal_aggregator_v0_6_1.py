#!/usr/bin/env python3
"""V0.6.1 step 5: goal status derived from task graph."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-goal-aggregator-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("goal_agg_v061", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BASE = REPO
    return mod


def make_db(statuses=None):
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(goal_id) REFERENCES goals(id)
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
        "INSERT INTO goals(description, status) VALUES ('g', 'active')"
    )
    if statuses:
        for i, status in enumerate(statuses, start=1):
            db.execute(
                """INSERT INTO tasks(
                       goal_id, task_key, title, description, status, priority
                   ) VALUES (1, ?, ?, ?, ?, 5)""",
                (f"T-{i}", f"task-{i}", f"desc-{i}", status),
            )
    db.commit()
    return path, db


def test_derive_table():
    agg = load()
    assert agg.derive_goal_status([]) is None
    assert agg.derive_goal_status(["completed", "completed"]) == "completed"
    assert agg.derive_goal_status(["completed", "blocked"]) == "blocked"
    assert agg.derive_goal_status(["completed", "pending"]) == "active"
    assert agg.derive_goal_status(["failed", "completed"]) == "active"
    assert agg.derive_goal_status(["running"]) == "active"
    print("PASS derive_goal_status table")


def test_sync_completed_and_blocked():
    agg = load()

    path, db = make_db(["completed", "completed", "completed"])
    try:
        result = agg.sync_goal_status(db, 1)
        assert result["derived"] == "completed"
        assert result["changed"] is True
        assert result["goal"]["status"] == "completed"
    finally:
        db.close()
        path.unlink(missing_ok=True)

    path, db = make_db(["completed", "blocked", "pending"])
    try:
        result = agg.sync_goal_status(db, 1)
        assert result["derived"] == "blocked"
        assert result["goal"]["status"] == "blocked"
        assert result["task_summary"]["blocked"] == 1
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS sync completed and blocked")


def test_sync_no_tasks_and_cancelled():
    agg = load()
    path, db = make_db(statuses=None)
    try:
        result = agg.sync_goal_status(db, 1)
        assert result["derived"] is None
        assert result["changed"] is False
        assert result["goal"]["status"] == "active"
    finally:
        db.close()
        path.unlink(missing_ok=True)

    path, db = make_db(["blocked"])
    try:
        db.execute("UPDATE goals SET status = 'cancelled' WHERE id = 1")
        db.commit()
        result = agg.sync_goal_status(db, 1)
        assert result["changed"] is False
        assert result["goal"]["status"] == "cancelled"
        forced = agg.sync_goal_status(db, 1, force=True)
        assert forced["changed"] is True
        assert forced["goal"]["status"] == "blocked"
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS sync no_tasks and cancelled")


def test_sync_with_template_expand():
    """End-to-end: expand template, complete all tasks, sync → completed."""
    agg = load()
    spec = importlib.util.spec_from_file_location(
        "tpl",
        REPO / "bin" / "anu-template-planner-v0.1.py",
    )
    plan_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plan_mod)
    plan_mod.BASE = REPO

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

    try:
        plan_mod.expand_goal(
            db, 1, "sandbox-test-project", plan_id="plan-agg-1"
        )
        mid = agg.sync_goal_status(db, 1)
        assert mid["derived"] == "active"
        assert mid["goal"]["status"] == "active"

        db.execute("UPDATE tasks SET status = 'completed' WHERE goal_id = 1")
        db.commit()
        done = agg.sync_goal_status(db, 1)
        assert done["derived"] == "completed"
        assert done["changed"] is True
        assert done["goal"]["status"] == "completed"
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS sync after template expand + complete-all")


if __name__ == "__main__":
    print("===== ANU V0.6.1 GOAL AGGREGATOR REGRESSION =====")
    test_derive_table()
    test_sync_completed_and_blocked()
    test_sync_no_tasks_and_cancelled()
    test_sync_with_template_expand()
    print("GOAL_AGGREGATOR_TEST_COMPLETE")
