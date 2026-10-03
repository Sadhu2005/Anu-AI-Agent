#!/usr/bin/env python3

import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"


def load_scheduler():
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_waiting_rewake_v0_5_7",
        SCHEDULER,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load scheduler: {SCHEDULER}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    db.executescript(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            goal_id INTEGER,
            parent_task_id INTEGER,
            task_key TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL,
            priority INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (task_id, depends_on_task_id),
            FOREIGN KEY(task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE,
            FOREIGN KEY(depends_on_task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE
        );
        """
    )

    db.executemany(
        """
        INSERT INTO tasks
        (id, task_key, title, description, status, priority, attempts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1001,
                "prerequisite",
                "Prerequisite",
                "",
                "pending",
                10,
                0,
            ),
            (
                1002,
                "dependent",
                "Dependent",
                "",
                "pending",
                5,
                0,
            ),
        ],
    )

    db.execute(
        """
        INSERT INTO task_dependencies(
            task_id,
            depends_on_task_id
        )
        VALUES (?, ?)
        """,
        (1002, 1001),
    )

    db.commit()
    return db


def test_waiting_dependent_becomes_wake_after_prerequisite():
    scheduler = load_scheduler()
    db = build_db()

    try:
        initial_ready = scheduler.discover_ready_tasks(db)

        assert initial_ready == [
            {
                "task_id": 1001,
                "status": "pending",
                "priority": 10,
                "attempts": 0,
            }
        ]

        assert db.execute(
            "SELECT status FROM tasks WHERE id = 1002"
        ).fetchone()[0] == "pending"

        waiting_plan = scheduler.build_dependency_wake_plan(
            db,
            1001,
        )

        assert waiting_plan == [
            {
                "task_id": 1002,
                "action": "WAIT",
                "current_status": "pending",
                "current_attempts": 0,
                "state": "WAITING",
                "reason": "PREREQUISITES_INCOMPLETE",
            }
        ]

        db.execute(
            "UPDATE tasks SET status = 'completed' WHERE id = 1001"
        )
        db.commit()

        ready = scheduler.discover_ready_tasks(db)

        assert ready == [
            {
                "task_id": 1002,
                "status": "pending",
                "priority": 5,
                "attempts": 0,
            }
        ]

        wake_plan = scheduler.build_ready_task_wake_plan(
            db,
            ready,
        )

        assert wake_plan == [
            {
                "task_id": 1002,
                "action": "WAKE",
                "current_status": "pending",
                "current_attempts": 0,
                "state": "READY",
            }
        ]

        assert db.execute(
            "SELECT status FROM tasks WHERE id = 1002"
        ).fetchone()[0] == "pending"

        print(
            "PASS WAITING dependent -> READY -> WAKE"
        )

    finally:
        db.close()


if __name__ == "__main__":
    print(
        "===== ANU V0.5.7 WAITING RE-WAKE REGRESSION ====="
    )

    test_waiting_dependent_becomes_wake_after_prerequisite()

    print("V0.5.7_WAITING_REWAKE_TEST_COMPLETE")
