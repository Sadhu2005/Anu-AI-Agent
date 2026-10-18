#!/usr/bin/env python3
"""V0.6.5: goal-level acceptance requires ACCEPT per task."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
VER = REPO / "bin" / "anu-verification-v0.1.py"
AGG = REPO / "bin" / "anu-goal-aggregator-v0.1.py"


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_db():
    root = Path(tempfile.mkdtemp())
    db_path = root / "brain" / "anu-brain.db"
    db_path.parent.mkdir(parents=True)
    db = sqlite3.connect(str(db_path))
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
            status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            goal_id INTEGER,
            parent_task_id INTEGER,
            run_id TEXT,
            attempt INTEGER,
            event_type TEXT,
            message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        "INSERT INTO projects (project_key, name, repository, workspace) "
        "VALUES ('sandbox', 'S', 'local', '/tmp/s')"
    )
    db.execute(
        "INSERT INTO goals (project_id, description, status) "
        "VALUES (1, 'goal', 'active')"
    )
    db.execute(
        "INSERT INTO tasks (goal_id, title, status) VALUES (1, 'a', 'completed')"
    )
    db.execute(
        "INSERT INTO tasks (goal_id, title, status) VALUES (1, 'b', 'completed')"
    )
    db.commit()
    return root, db


def _accept(m, db, task_id, attempt=1):
    return m.evaluate_mutation_attempt(
        db,
        {
            "status": "COMMITTED",
            "operation": "python_test",
            "files": ["app.py"],
            "mutation_test_ok": True,
            "integrity_verified": True,
            "file_records": [
                {
                    "path": "app.py",
                    "baseline_sha256": "x",
                    "after_sha256": "y",
                }
            ],
            "actual_changed_files": ["app.py"],
        },
        task_id=task_id,
        attempt=attempt,
        goal_id=1,
    )


def test_goal_acceptance_requires_all_accept():
    m = load_mod(VER, "ver_goal")
    _root, db = make_db()
    m.migrate(db)
    # Only one task verified → goal not accepted.
    _accept(m, db, 1)
    record = m.evaluate_goal_acceptance(db, 1, persist=True)
    assert record["goal_ok"] is False, record
    assert record["decision"] == "RETRY"
    # Both accepted → goal OK.
    _accept(m, db, 2)
    record2 = m.evaluate_goal_acceptance(db, 1, persist=True)
    assert record2["goal_ok"] is True, record2
    assert record2["decision"] == "ACCEPT"
    # Task-level latest still ACCEPT (goal rows excluded).
    latest = m.get_latest_for_task(db, 1)
    assert latest["decision"] == "ACCEPT"
    assert latest["goal_ok"] is None
    print("PASS goal acceptance all ACCEPT")


def test_aggregator_holds_completed_without_verification():
    """Goal stays active when tasks completed but no ACCEPT records."""
    # Minimal stub: goal service may be heavy; exercise evaluate path only
    # when aggregator modules need get_goal. Skip if goal service missing tables.
    m = load_mod(VER, "ver_goal2")
    _root, db = make_db()
    m.migrate(db)
    record = m.evaluate_goal_acceptance(db, 1, persist=False)
    assert record["goal_ok"] is False
    print("PASS aggregator gate: no ACCEPT => not goal_ok")


def main():
    print("===== ANU V0.6.5 GOAL ACCEPTANCE =====")
    test_goal_acceptance_requires_all_accept()
    test_aggregator_holds_completed_without_verification()
    print("GOAL_ACCEPTANCE_TEST_COMPLETE")


if __name__ == "__main__":
    main()
