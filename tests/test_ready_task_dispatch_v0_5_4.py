#!/usr/bin/env python3

import copy
import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")


def load_scheduler():
    path = BASE / "bin" / "anu-task-scheduler-v0.1.py"
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_dispatch_test",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load scheduler module: {path}")

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
            (901, "ready-a", "Ready A", "", "pending", 10, 0),
            (902, "ready-b", "Ready B", "", "pending", 3, 1),
            (903, "waiting", "Waiting", "", "pending", 8, 0),
            (904, "blocked-prereq", "Blocked prerequisite", "", "blocked", 1, 3),
        ],
    )

    db.execute(
        """
        INSERT INTO task_dependencies(task_id, depends_on_task_id)
        VALUES (?, ?)
        """,
        (903, 904),
    )

    db.commit()
    return db


def snapshot_db(db):
    return (
        [
            tuple(row)
            for row in db.execute(
                """
                SELECT id, status, priority, attempts
                FROM tasks
                ORDER BY id
                """
            ).fetchall()
        ],
        [
            tuple(row)
            for row in db.execute(
                """
                SELECT id, task_id, depends_on_task_id
                FROM task_dependencies
                ORDER BY id
                """
            ).fetchall()
        ],
    )


def test_dispatch_routes_ready_tasks():
    scheduler = load_scheduler()
    db = build_db()
    calls = []

    def wake_handler(plan_item):
        calls.append(copy.deepcopy(plan_item))
        return {
            "task_id": plan_item["task_id"],
            "result": "accepted",
        }

    handlers = {
        "WAKE": wake_handler,
        "WAIT": lambda item: {"result": "wait"},
        "BLOCK": lambda item: {"result": "block"},
    }

    results = scheduler.dispatch_ready_task_wakes(db, handlers)

    assert calls == [
        {
            "task_id": 901,
            "action": "WAKE",
            "current_status": "pending",
            "current_attempts": 0,
            "state": "READY",
        },
        {
            "task_id": 902,
            "action": "WAKE",
            "current_status": "pending",
            "current_attempts": 1,
            "state": "READY",
        },
    ]

    assert results == [
        {"task_id": 901, "result": "accepted"},
        {"task_id": 902, "result": "accepted"},
    ]

    print("PASS dispatch routes ready tasks")


def test_dispatch_preserves_database_state():
    scheduler = load_scheduler()
    db = build_db()

    before = snapshot_db(db)

    handlers = {
        "WAKE": lambda item: {"task_id": item["task_id"]},
        "WAIT": lambda item: {"result": "wait"},
        "BLOCK": lambda item: {"result": "block"},
    }

    scheduler.dispatch_ready_task_wakes(db, handlers)

    after = snapshot_db(db)

    assert before == after

    print("PASS dispatch boundary is read-only")


def test_dispatch_preserves_candidate_order():
    scheduler = load_scheduler()
    db = build_db()
    observed = []

    def wake_handler(item):
        observed.append(item["task_id"])
        return item["task_id"]

    handlers = {
        "WAKE": wake_handler,
        "WAIT": lambda item: None,
        "BLOCK": lambda item: None,
    }

    scheduler.dispatch_ready_task_wakes(db, handlers)

    assert observed == [901, 902]

    print("PASS dispatch preserves deterministic order")


def test_no_ready_tasks_produce_no_handler_calls():
    scheduler = load_scheduler()
    db = build_db()

    db.execute("UPDATE tasks SET status = 'completed' WHERE id IN (901, 902)")
    db.commit()

    calls = []

    def wake_handler(item):
        calls.append(item)
        return item

    handlers = {
        "WAKE": wake_handler,
        "WAIT": lambda item: None,
        "BLOCK": lambda item: None,
    }

    results = scheduler.dispatch_ready_task_wakes(db, handlers)

    assert results == []
    assert calls == []

    print("PASS no ready tasks -> no dispatch")


def test_handlers_are_required_before_execution():
    scheduler = load_scheduler()
    db = build_db()

    try:
        scheduler.dispatch_ready_task_wakes(
            db,
            {
                "WAIT": lambda item: None,
                "BLOCK": lambda item: None,
            },
        )
    except (KeyError, ValueError, TypeError):
        print("PASS missing WAKE handler rejected")
        return

    raise AssertionError("Expected missing WAKE handler to be rejected")


def main():
    print("===== ANU V0.5.4 READY TASK DISPATCH REGRESSION =====")

    test_dispatch_routes_ready_tasks()
    test_dispatch_preserves_database_state()
    test_dispatch_preserves_candidate_order()
    test_no_ready_tasks_produce_no_handler_calls()
    test_handlers_are_required_before_execution()

    print("READY_TASK_DISPATCH_TEST_COMPLETE")


if __name__ == "__main__":
    main()
