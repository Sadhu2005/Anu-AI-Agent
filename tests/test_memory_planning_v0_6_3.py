#!/usr/bin/env python3
"""V0.6.3: repeated failure memory changes template plan content."""

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


def load_planner():
    planner = load(
        REPO / "bin" / "anu-template-planner-v0.1.py",
        "planner_mem_t",
    )
    planner.BASE = REPO
    return planner


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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(goal_id) REFERENCES goals(id),
            FOREIGN KEY(parent_task_id) REFERENCES tasks(id)
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
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            observation_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        "INSERT INTO goals(description, status) VALUES (?, 'pending')",
        ("Improve sandbox project",),
    )
    db.commit()
    return path, db


def test_plan_changes_when_repeated_failure_in_memory():
    planner = load_planner()
    mem = load(
        REPO / "bin" / "anu-observation-memory-v0.1.py",
        "obs_mem_plan_t",
    )

    path, db = make_db()
    try:
        clean = planner.expand_goal(
            db, 1, "sandbox-test-project", template_name="improve_sandbox"
        )
        clean_desc = clean["plan"]["body"]["actions"][0]["description"]
        assert "memory: repeated failure" not in clean_desc

        mem.migrate(db)
        for _ in range(2):
            mem.record_capability_result(
                db,
                project_key="sandbox-test-project",
                capability="python.test",
                ok=False,
                exit_code=1,
                error="unittest fail",
            )

        db.execute(
            "INSERT INTO goals(description, status) VALUES (?, 'pending')",
            ("Second goal",),
        )
        db.commit()
        goal2 = int(
            db.execute("SELECT id FROM goals ORDER BY id DESC LIMIT 1").fetchone()[0]
        )

        touched = planner.expand_goal(
            db, goal2, "sandbox-test-project", template_name="improve_sandbox"
        )
        assert touched["memory"]["repeated_failure"] is True
        first = touched["plan"]["body"]["actions"][0]
        assert "memory: repeated failure" in first["description"]
        assert first["parameters"].get("memory_repeated_failure") is True
        assert first["parameters"].get("memory_hints")
        print("PASS repeated failure memory changes plan description/params")
    finally:
        db.close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    print("===== ANU V0.6.3 MEMORY PLANNING =====")
    test_plan_changes_when_repeated_failure_in_memory()
    print("MEMORY_PLANNING_TEST_COMPLETE")
