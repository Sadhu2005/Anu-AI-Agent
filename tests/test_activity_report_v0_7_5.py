#!/usr/bin/env python3
"""V0.7.5: activity report goal + recent (read-only brain assembly)."""

import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-activity-report-v0.1.py"


def load_report():
    spec = importlib.util.spec_from_file_location("activity_report_v075", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BASE = REPO
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
            project_key TEXT NOT NULL UNIQUE
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(goal_id) REFERENCES goals(id)
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
        "INSERT INTO projects(project_key) VALUES ('sandbox-test-project')"
    )
    db.execute(
        "INSERT INTO goals(project_id, description, status) "
        "VALUES (1, 'Oldest goal', 'completed')"
    )
    db.execute(
        "INSERT INTO goals(project_id, description, status) "
        "VALUES (1, 'Newest goal LLM smoke', 'active')"
    )
    db.commit()

    # Plan for goal 2 via plan service
    plans = _load_plans()
    plans.BASE = REPO
    plans.migrate(db)
    body = {
        "plan_id": "plan-llm-test",
        "goal_id": "2",
        "actions": [
            {
                "action_id": "inspect-1",
                "goal_id": "2",
                "kind": "TASK",
                "description": "inspect",
                "parameters": {
                    "planner": "llm",
                    "template": "llm",
                    "role": "inspect",
                    "operation": "python_test",
                },
            },
            {
                "action_id": "compile-1",
                "goal_id": "2",
                "kind": "TASK",
                "description": "compile",
                "parameters": {
                    "planner": "llm",
                    "template": "llm",
                    "role": "compile",
                    "operation": "python_compile",
                },
            },
        ],
    }
    plans.save_plan(db, 2, body, status="active")

    # Tasks for goal 2
    db.execute(
        """INSERT INTO tasks(goal_id, task_key, title, description, status, attempts)
           VALUES (2, 'T1', 'llm:inspect', 'inspect', 'completed', 1)"""
    )
    db.execute(
        """INSERT INTO tasks(goal_id, task_key, title, description, status, attempts)
           VALUES (2, 'T2', 'llm:compile', 'compile', 'failed', 2)"""
    )
    db.execute(
        """INSERT INTO task_execution_specs(task_id, project, changes_file, operation)
           VALUES (1, 'sandbox-test-project', 'changes.json', 'python_test')"""
    )
    db.execute(
        """INSERT INTO task_execution_specs(task_id, project, changes_file, operation)
           VALUES (2, 'sandbox-test-project', 'changes.json', 'python_compile')"""
    )
    db.execute(
        """INSERT INTO agent_events(event_type, message, goal_id, created_at)
           VALUES ('PLAN_SAVED', 'plan saved', 2, '2026-10-18T12:00:00+00:00')"""
    )
    db.execute(
        """INSERT INTO agent_events(event_type, message, goal_id, created_at)
           VALUES ('GOAL_STATUS_CHANGED', 'active', 2, '2026-10-18T12:01:00+00:00')"""
    )
    db.commit()
    return path, db


def _load_plans():
    path = REPO / "bin" / "anu-goal-plan-service-v0.1.py"
    spec = importlib.util.spec_from_file_location("plans_v075_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_goal_fields_and_summary():
    report = load_report()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    path, db = make_db()
    try:
        result = report.report_goal(db, 2)
        assert result["ok"] is True
        assert result["goal"]["id"] == 2
        assert result["goal"]["project_key"] == "sandbox-test-project"
        assert result["goal"]["description"] == "Newest goal LLM smoke"
        assert result["plan"]["source_hint"] == "llm"
        assert result["plan"]["action_count"] == 2
        assert len(result["tasks"]) == 2
        assert result["tasks"][0]["operation"] == "python_test"
        assert result["tasks"][1]["status"] == "failed"
        assert result["summary"]["task_total"] == 2
        assert result["summary"]["task_completed"] == 1
        assert result["summary"]["task_failed"] == 1
        assert result["summary"]["last_event_at"] is not None
        assert len(result["events"]) >= 1
        assert result["events"][0]["event_type"] == "GOAL_STATUS_CHANGED"
    finally:
        db.close()
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS report_goal fields and summary")


def test_report_goal_missing():
    report = load_report()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    path, db = make_db()
    try:
        result = report.report_goal(db, 999)
        assert result["ok"] is False
        assert "not found" in result["error"]
    finally:
        db.close()
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS report_goal missing -> not ok")


def test_report_recent_newest_first():
    report = load_report()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    path, db = make_db()
    try:
        result = report.report_recent(db, limit=5)
        assert result["ok"] is True
        assert result["count"] == 2
        ids = [r["goal"]["id"] for r in result["reports"] if r.get("ok")]
        assert ids == [2, 1]
    finally:
        db.close()
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS report_recent newest first")


def test_cli_goal_exit_codes():
    report = load_report()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    path, db = make_db()
    db.close()
    try:
        code_ok = report.main(["--db", str(path), "goal", "--goal-id", "2"])
        code_bad = report.main(["--db", str(path), "goal", "--goal-id", "999"])
        assert code_ok == 0
        assert code_bad == 1
    finally:
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS CLI goal exit codes")


if __name__ == "__main__":
    print("===== ANU V0.7.5 ACTIVITY REPORT =====")
    test_report_goal_fields_and_summary()
    test_report_goal_missing()
    test_report_recent_newest_first()
    test_cli_goal_exit_codes()
    print("ACTIVITY_REPORT_TEST_COMPLETE")
