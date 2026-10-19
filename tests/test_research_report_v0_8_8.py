#!/usr/bin/env python3
"""V0.8.8: activity reports surface research / github findings."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-activity-report-v0.1.py"


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
            name TEXT NOT NULL DEFAULT 'Sandbox',
            repository TEXT NOT NULL DEFAULT 'local',
            workspace TEXT NOT NULL DEFAULT '/tmp',
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
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL
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
        CREATE TABLE research (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            url TEXT,
            title TEXT,
            summary TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        "INSERT INTO projects(project_key, name, repository, workspace) "
        "VALUES ('sandbox-test-project', 'Sandbox', 'local', '/tmp/s')"
    )
    db.execute(
        "INSERT INTO goals(project_id, description, status) "
        "VALUES (1, 'research report smoke', 'active')"
    )
    db.execute(
        "INSERT INTO tasks(goal_id, title, status) "
        "VALUES (1, 'multi_stage_goal:research', 'completed')"
    )
    db.execute(
        "INSERT INTO task_execution_specs(task_id, project, changes_file, operation) "
        "VALUES (1, 'sandbox-test-project', 'changes.json', 'research')"
    )
    db.commit()
    return path, db


def test_goal_report_includes_research_block():
    rep = load(MOD, "rep_v088")
    rep.BASE = REPO
    path, db = make_db()
    try:
        db.execute(
            """INSERT INTO research (task_id, url, title, summary, created_at)
               VALUES (1, ?, ?, ?, ?)""",
            (
                "https://en.wikipedia.org/wiki/Raspberry_Pi",
                "Raspberry Pi",
                "A series of small single-board computers.",
                "2026-10-19T12:00:00+00:00",
            ),
        )
        db.execute(
            """INSERT INTO research (task_id, url, title, summary, created_at)
               VALUES (1, ?, ?, ?, ?)""",
            (
                "https://github.com/Sadhu2005/Anu-AI-Agent",
                "Sadhu2005/Anu-AI-Agent",
                '{"full_name": "Sadhu2005/Anu-AI-Agent"}',
                "2026-10-19T12:30:00+00:00",
            ),
        )
        db.commit()

        result = rep.report_goal(db, 1)
        assert result["ok"] is True, result
        block = result["research"]
        assert block["count"] == 2
        assert block["latest"]["title"] == "Sadhu2005/Anu-AI-Agent"
        assert block["latest"]["task_id"] == 1
        assert len(block["items"]) == 2
        assert block["items"][0]["task_title"] == "multi_stage_goal:research"
        print("PASS goal report includes research block")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def test_no_research_table_is_safe():
    rep = load(MOD, "rep_v088b")
    rep.BASE = REPO
    path, db = make_db()
    try:
        db.execute("DROP TABLE research")
        db.commit()
        result = rep.report_goal(db, 1)
        assert result["ok"] is True
        assert result["research"]["count"] == 0
        assert result["research"]["latest"] is None
        print("PASS missing research table is safe")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def test_project_summary_aggregates_research():
    rep = load(MOD, "rep_v088c")
    rep.BASE = REPO
    path, db = make_db()
    try:
        db.execute(
            """INSERT INTO research (task_id, url, title, summary)
               VALUES (1, 'https://example.com', 'Example Finding', 'x')"""
        )
        db.commit()

        def fake_dossier(db_arg, key):
            return {
                "project_key": key,
                "registered": True,
                "open_goals": 1,
                "open_tasks": 1,
                "verification_hint_count": 0,
                "memory_repeated_failure": False,
                "recent_goal_ids": [1],
            }

        rep._dossier_best_effort = fake_dossier
        project = rep.report_project(db, "sandbox-test-project")
        assert project["ok"] is True, project
        assert project["summary"]["research_count"] == 1
        assert project["summary"]["latest_research_title"] == "Example Finding"
        print("PASS project summary aggregates research")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def main():
    test_goal_report_includes_research_block()
    test_no_research_table_is_safe()
    test_project_summary_aggregates_research()
    print("ALL_PASS test_research_report_v0_8_8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
