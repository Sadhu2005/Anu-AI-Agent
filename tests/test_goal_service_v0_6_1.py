#!/usr/bin/env python3
"""V0.6.1 step 2: goal service CRUD against a temp Brain fragment."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-goal-service-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("goal_service_v061", MODULE)
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id)
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
        """INSERT INTO projects(project_key, name, repository, workspace)
           VALUES ('demo', 'Demo', 'repo', 'ws')"""
    )
    db.commit()
    return path, db


def test_create_get_list_and_status():
    gs = load()
    path, db = make_db()
    try:
        goal = gs.create_goal(db, "Improve sandbox reliability", project_id=1)
        assert goal["id"] >= 1
        assert goal["status"] == "pending"
        assert goal["project_id"] == 1
        assert goal["description"] == "Improve sandbox reliability"

        fetched = gs.get_goal(db, goal["id"])
        assert fetched == goal

        listed = gs.list_goals(db, project_id=1)
        assert [g["id"] for g in listed] == [goal["id"]]

        updated = gs.update_goal_status(
            db, goal["id"], "active", reason="plan attached"
        )
        assert updated["status"] == "active"

        same = gs.update_goal_status(db, goal["id"], "active")
        assert same["status"] == "active"

        events = db.execute(
            "SELECT event_type FROM agent_events WHERE goal_id = ? ORDER BY id",
            (goal["id"],),
        ).fetchall()
        types = [row["event_type"] for row in events]
        assert types == ["GOAL_CREATED", "GOAL_STATUS_CHANGED"]
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS create/get/list/update_goal_status + events")


def test_validation_and_missing_project():
    gs = load()
    path, db = make_db()
    try:
        try:
            gs.create_goal(db, "   ")
            raise AssertionError("empty description should fail")
        except ValueError:
            pass

        try:
            gs.create_goal(db, "ok", project_id=999)
            raise AssertionError("missing project should fail")
        except ValueError as exc:
            assert "project does not exist" in str(exc)

        try:
            gs.update_goal_status(db, 1, "nope")
            raise AssertionError("bad status should fail")
        except ValueError:
            pass

        try:
            gs.update_goal_status(db, 999, "active")
            raise AssertionError("missing goal should fail")
        except ValueError as exc:
            assert "does not exist" in str(exc)

        assert gs.get_goal(db, 1) is None
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS validation and missing project/goal")


def test_cli_create_roundtrip(tmp_path=None):
    gs = load()
    path, db = make_db()
    db.close()
    try:
        rc = gs.main([
            "--db", str(path),
            "create",
            "--description", "CLI goal",
        ])
        assert rc == 0

        db2 = gs.db_connect(path)
        goals = gs.list_goals(db2)
        assert len(goals) == 1
        assert goals[0]["description"] == "CLI goal"
        db2.close()
    finally:
        path.unlink(missing_ok=True)

    print("PASS CLI create roundtrip")


if __name__ == "__main__":
    print("===== ANU V0.6.1 GOAL SERVICE REGRESSION =====")
    test_create_get_list_and_status()
    test_validation_and_missing_project()
    test_cli_create_roundtrip()
    print("GOAL_SERVICE_TEST_COMPLETE")
