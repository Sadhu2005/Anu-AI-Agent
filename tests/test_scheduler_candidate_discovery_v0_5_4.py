#!/usr/bin/env python3

import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"


def load_scheduler():
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_candidate_test",
        SCHEDULER,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load scheduler: {SCHEDULER}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_task(db, task_id, title, status, priority, attempts=0):
    db.execute(
        """
        INSERT INTO tasks(
            id,
            title,
            status,
            priority,
            attempts
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            task_id,
            title,
            status,
            priority,
            attempts,
        ),
    )


def create_dependency(db, task_id, prerequisite_id):
    db.execute(
        """
        INSERT INTO task_dependencies(
            task_id,
            depends_on_task_id,
            created_at
        )
        VALUES (?, ?, ?)
        """,
        (
            task_id,
            prerequisite_id,
            "2026-10-03T00:00:00Z",
        ),
    )


def setup_db(scheduler):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    db.execute(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
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
        )
        """
    )

    db.execute(
        """
        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, depends_on_task_id),
            CHECK(task_id != depends_on_task_id),
            FOREIGN KEY(task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE,
            FOREIGN KEY(depends_on_task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE
        )
        """
    )

    # Ready immediately: no dependencies.
    create_task(db, 901, "READY-HIGH", "pending", 10)

    # Ready immediately, but lower priority.
    create_task(db, 902, "READY-LOW", "pending", 3)

    # Waiting: prerequisite not complete.
    create_task(db, 903, "WAITING", "pending", 9)
    create_task(db, 904, "PREREQ", "running", 5)
    create_dependency(db, 903, 904)

    # Completed task must not be returned.
    create_task(db, 905, "COMPLETED", "completed", 20)

    # Blocked task must not be returned.
    create_task(db, 906, "BLOCKED", "blocked", 20)

    # Failed task is outside this first candidate-discovery contract.
    create_task(db, 907, "FAILED", "failed", 20, attempts=1)

    db.commit()

    scheduler._candidate_test_db_marker = True
    return db


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            f"{message}\nexpected={expected!r}\nactual={actual!r}"
        )


def main():
    print(
        "===== ANU V0.5.4 SCHEDULER CANDIDATE DISCOVERY REGRESSION ====="
    )

    scheduler = load_scheduler()

    assert hasattr(scheduler, "discover_ready_tasks"), (
        "scheduler must expose discover_ready_tasks(db)"
    )

    db = setup_db(scheduler)

    try:
        before = db.execute(
            """
            SELECT
                id,
                status,
                priority,
                attempts
            FROM tasks
            ORDER BY id
            """
        ).fetchall()

        result = scheduler.discover_ready_tasks(db)

        assert_equal(
            result,
            [
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
                    "attempts": 0,
                },
            ],
            "candidate discovery must return only dependency-ready pending tasks "
            "in deterministic priority order",
        )
        print("PASS ready candidate discovery")

        after = db.execute(
            """
            SELECT
                id,
                status,
                priority,
                attempts
            FROM tasks
            ORDER BY id
            """
        ).fetchall()

        assert_equal(
            after,
            before,
            "candidate discovery must be strictly read-only",
        )
        print("PASS candidate discovery is read-only")

        assert_equal(
            [item["task_id"] for item in result],
            [901, 902],
            "candidate ordering must be deterministic",
        )
        print("PASS deterministic candidate ordering")

        assert 903 not in [item["task_id"] for item in result]
        print("PASS waiting dependency excluded")

        assert 905 not in [item["task_id"] for item in result]
        assert 906 not in [item["task_id"] for item in result]
        assert 907 not in [item["task_id"] for item in result]
        print("PASS terminal/failed tasks excluded")

        second = scheduler.discover_ready_tasks(db)

        assert_equal(
            second,
            result,
            "repeated discovery must produce identical results",
        )
        print("PASS repeated discovery deterministic")

        print(
            "SCHEDULER_CANDIDATE_DISCOVERY_TEST_COMPLETE"
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
