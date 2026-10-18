#!/usr/bin/env python3
"""V0.7.0: reboot mid-goal does not duplicate plan/tasks."""

import importlib.util
import os
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
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
            retry_after TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, depends_on_task_id),
            CHECK(task_id != depends_on_task_id)
        );
        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        CREATE TABLE mutation_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT,
            task_id INTEGER,
            status TEXT,
            idempotency_key TEXT,
            details_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        """INSERT INTO projects(project_key, name, repository, workspace)
           VALUES ('sandbox-test-project', 'Sandbox', 'local',
                   'workspace/projects/sandbox-test-project')"""
    )
    db.execute(
        """INSERT INTO goals(project_id, description, status)
           VALUES (1, 'Mid-goal reboot', 'pending')"""
    )
    db.commit()
    return path, db


def test_startup_and_expand_do_not_duplicate():
    cycle = load(REPO / "bin" / "anu-goal-cycle-v0.1.py", "goal_cycle_reboot")
    cycle.BASE = REPO
    startup = load(
        REPO / "bin" / "anu-startup-recovery-v0.1.py",
        "startup_reboot",
    )
    path, db = make_db()
    root = Path(tempfile.mkdtemp())
    (root / "runtime").mkdir()
    state_file = root / "runtime" / "anu-state.json"
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    try:
        expand = cycle.expand_pending_goals(db, max_goals=1)
        assert expand["expanded_count"] == 1
        task_count = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE goal_id = 1"
        ).fetchone()[0]
        plan_count = db.execute(
            "SELECT COUNT(*) FROM goal_plans WHERE goal_id = 1"
        ).fetchone()[0]

        # Simulate mid-goal: one task waiting for resource/approval.
        first_task = db.execute(
            "SELECT id FROM tasks WHERE goal_id = 1 ORDER BY id LIMIT 1"
        ).fetchone()[0]
        db.execute(
            "UPDATE tasks SET status = 'waiting' WHERE id = ?",
            (first_task,),
        )
        db.commit()

        summary = startup.run_startup_sequence(
            db_path=str(path),
            state_path=str(state_file),
            skip_mutation_recovery=True,
        )
        assert summary.get("ok") is True or "inventory" in summary

        # Re-open after startup closed its connection.
        db.close()
        db = sqlite3.connect(str(path))
        db.row_factory = sqlite3.Row
        again = cycle.expand_pending_goals(db, max_goals=1)
        assert again["expanded_count"] == 0, again
        assert db.execute(
            "SELECT COUNT(*) FROM tasks WHERE goal_id = 1"
        ).fetchone()[0] == task_count
        assert db.execute(
            "SELECT COUNT(*) FROM goal_plans WHERE goal_id = 1"
        ).fetchone()[0] == plan_count
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
        db.close()
        path.unlink(missing_ok=True)
    print("PASS reboot mid-goal: no duplicate plan/tasks")


if __name__ == "__main__":
    print("===== ANU V0.7.0 GOAL REBOOT RESUME =====")
    test_startup_and_expand_do_not_duplicate()
    print("GOAL_REBOOT_RESUME_TEST_COMPLETE")
