#!/usr/bin/env python3

import copy
import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")


def load_scheduler():
    path = BASE / "bin" / "anu-task-scheduler-v0.1.py"
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_ready_wake_plan_test",
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
            (903, "blocked-candidate", "Blocked", "", "pending", 8, 0),
            (904, "prerequisite", "Prerequisite", "", "blocked", 1, 3),
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
        db.execute(
            """
            SELECT id, status, priority, attempts
            FROM tasks
            ORDER BY id
            """
        ).fetchall(),
        db.execute(
            """
            SELECT task_id, depends_on_task_id
            FROM task_dependencies
            ORDER BY task_id, depends_on_task_id
            """
        ).fetchall(),
    )


def test_ready_candidates_become_wake_plan():
    scheduler = load_scheduler()
    db = build_db()

    candidates = [
        {
            "task_id": 901,
            "status": "pending",
            "priority": 10,
            "attempts": 0,
        },
        {
            "task_id": 902,
            "status": "pending",
            "priority": 3,
            "attempts": 1,
        },
    ]

    plan = scheduler.build_ready_task_wake_plan(db, candidates)

    assert plan == [
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

    print("PASS ready candidates -> WAKE plan")


def test_candidate_order_is_preserved():
    scheduler = load_scheduler()
    db = build_db()

    candidates = [
        {
            "task_id": 902,
            "status": "pending",
            "priority": 3,
            "attempts": 1,
        },
        {
            "task_id": 901,
            "status": "pending",
            "priority": 10,
            "attempts": 0,
        },
    ]

    plan = scheduler.build_ready_task_wake_plan(db, candidates)

    assert [item["task_id"] for item in plan] == [902, 901]

    print("PASS candidate ordering preserved")


def test_inputs_and_database_are_read_only():
    scheduler = load_scheduler()
    db = build_db()

    candidates = [
        {
            "task_id": 901,
            "status": "pending",
            "priority": 10,
            "attempts": 0,
        },
    ]

    original_candidates = copy.deepcopy(candidates)
    before = snapshot_db(db)

    scheduler.build_ready_task_wake_plan(db, candidates)

    after = snapshot_db(db)

    assert candidates == original_candidates
    assert before == after

    print("PASS wake-plan builder is read-only")


def test_stale_non_ready_candidate_is_rejected():
    scheduler = load_scheduler()
    db = build_db()

    candidates = [
        {
            "task_id": 903,
            "status": "pending",
            "priority": 8,
            "attempts": 0,
        },
    ]

    try:
        scheduler.build_ready_task_wake_plan(db, candidates)
    except (ValueError, RuntimeError):
        print("PASS stale/non-READY candidate rejected")
        return

    raise AssertionError("Expected stale/non-READY candidate to be rejected")


def test_malformed_candidate_is_rejected():
    scheduler = load_scheduler()
    db = build_db()

    malformed = [
        {
            "task_id": 901,
            "status": "pending",
            "priority": 10,
        }
    ]

    try:
        scheduler.build_ready_task_wake_plan(db, malformed)
    except (KeyError, TypeError, ValueError):
        print("PASS malformed candidate rejected")
        return

    raise AssertionError("Expected malformed candidate to be rejected")


def main():
    print("===== ANU V0.5.4 READY->WAKE PLAN REGRESSION =====")

    test_ready_candidates_become_wake_plan()
    test_candidate_order_is_preserved()
    test_inputs_and_database_are_read_only()
    test_stale_non_ready_candidate_is_rejected()
    test_malformed_candidate_is_rejected()

    print("READY_TASK_WAKE_PLAN_TEST_COMPLETE")


if __name__ == "__main__":
    main()
