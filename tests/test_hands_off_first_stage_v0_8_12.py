#!/usr/bin/env python3
"""V0.8.12: after expand, wake the first READY task for the goal."""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
CYCLE = REPO / "bin" / "anu-goal-cycle-v0.1.py"


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
            status TEXT NOT NULL DEFAULT 'active'
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
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            UNIQUE(task_id, depends_on_task_id)
        );
        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL
        );
        CREATE TABLE agent_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            plan_id TEXT,
            plan_json TEXT,
            status TEXT
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            goal_id INTEGER,
            event_type TEXT,
            message TEXT,
            metadata TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO projects(project_key, name, repository, workspace) "
        "VALUES ('sandbox-test-project', 'Sandbox', 'local', 'ws')"
    )
    db.execute(
        "INSERT INTO goals(project_id, description, status) "
        "VALUES (1, 'hands-off smoke', 'pending')"
    )
    db.commit()
    return path, db


def test_start_first_ready_selects_goal_task():
    cycle = load(CYCLE, "cycle_0812")
    cycle.BASE = REPO
    path, db = make_db()
    try:
        db.execute(
            "INSERT INTO tasks(goal_id, title, description, status, priority) "
            "VALUES (1, 'stage-a', 'survey', 'pending', 50)"
        )
        db.execute(
            "INSERT INTO tasks(goal_id, title, description, status, priority) "
            "VALUES (1, 'stage-b', 'design', 'pending', 49)"
        )
        db.execute(
            "INSERT INTO task_dependencies(task_id, depends_on_task_id) "
            "VALUES (2, 1)"
        )
        db.commit()

        class FakeSched:
            def discover_ready_tasks(self, db):
                return [
                    {"task_id": 1, "status": "pending", "priority": 50, "attempts": 0},
                ]

            def build_ready_task_wake_plan(self, db, selected):
                return {"items": selected}

            def build_production_agent_wake_execution_handler(self, db):
                def handle(item):
                    return {"ok": True, "task_id": item["task_id"], "woken": True}

                return handle

            def consume_dependency_wake_plan(self, plan, handlers):
                out = []
                for item in plan["items"]:
                    out.append(handlers["WAKE"](item))
                return out

        with mock.patch.object(cycle, "_load_scheduler", return_value=FakeSched()):
            result = cycle.start_first_ready_for_goal(db, 1)
        assert result["started"] is True
        assert result["task_ids"] == [1]
        assert result["results"][0]["woken"] is True
    finally:
        db.close()
        path.unlink(missing_ok=True)
    print("PASS start_first_ready_for_goal selects goal task")


def test_expand_includes_first_ready():
    cycle = load(CYCLE, "cycle_0812b")
    cycle.BASE = REPO
    path, db = make_db()
    try:
        # Bypass real planner/intel: seed expand path via mocked expand_goal.
        fake_planner = mock.Mock()
        fake_planner.expand_goal.return_value = {
            "template": "multi_stage_goal",
            "source": "template",
            "tasks": [{"task_id": 1}, {"task_id": 2}],
        }

        def fake_start(db, goal_id, *, max_tasks=1):
            return {
                "started": True,
                "goal_id": goal_id,
                "task_ids": [99],
                "results": [{"ok": True, "task_id": 99}],
            }

        with mock.patch.object(cycle, "_load_planner", return_value=fake_planner):
            with mock.patch.object(
                cycle, "start_first_ready_for_goal", side_effect=fake_start
            ):
                # goal_already_expanded checks plans — insert empty so False
                # by not having plans; ensure list_pending finds goal 1.
                out = cycle.expand_pending_goals(db, max_goals=1)

        assert out["expanded_count"] == 1, out
        fr = out["expanded"][0]["first_ready"]
        assert fr["started"] is True
        assert fr["task_ids"] == [99]
        fake_planner.expand_goal.assert_called_once()
    finally:
        db.close()
        path.unlink(missing_ok=True)
    print("PASS expand includes first_ready")


def test_start_first_ready_no_candidates():
    cycle = load(CYCLE, "cycle_0812c")
    cycle.BASE = REPO
    path, db = make_db()
    try:
        class FakeSched:
            def discover_ready_tasks(self, db):
                return []

        with mock.patch.object(cycle, "_load_scheduler", return_value=FakeSched()):
            result = cycle.start_first_ready_for_goal(db, 1)
        assert result["started"] is False
        assert result["reason"] == "no_ready_task"
    finally:
        db.close()
        path.unlink(missing_ok=True)
    print("PASS start_first_ready no candidates")


def main():
    test_start_first_ready_selects_goal_task()
    test_expand_includes_first_ready()
    test_start_first_ready_no_candidates()
    print("ALL_PASS test_hands_off_first_stage_v0_8_12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
