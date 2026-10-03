#!/usr/bin/env python3

import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"


def load_scheduler():
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_db_binding_test",
        SCHEDULER,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load scheduler: {SCHEDULER}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            f"{message}\nexpected={expected!r}\nactual={actual!r}"
        )


def setup_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    db.execute(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL
        )
        """
    )

    db.execute(
        """
        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE
        )
        """
    )

    db.execute(
        "INSERT INTO tasks(id, title) VALUES (?, ?)",
        (801, "DB-bound wake task"),
    )

    db.execute(
        """
        INSERT INTO task_execution_specs(
            task_id,
            project,
            changes_file,
            operation,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            801,
            "sandbox-test-project",
            "/tmp/db-bound-801.json",
            "python_test",
            "2026-10-03T00:00:00Z",
            "2026-10-03T00:00:00Z",
        ),
    )

    db.commit()
    return db


def main():
    print(
        "===== ANU V0.5.4 DB-BOUND WAKE HANDLER REGRESSION ====="
    )

    scheduler = load_scheduler()
    db = setup_db()

    calls = []

    def fake_start_task(
        task_id,
        project,
        changes_file,
        operation,
    ):
        calls.append(
            (
                task_id,
                project,
                changes_file,
                operation,
            )
        )
        return (
            {
                "task_id": task_id,
                "project": project,
                "changes_file": changes_file,
                "operation": operation,
            },
            0,
        )

    assert hasattr(
        scheduler,
        "build_db_bound_wake_execution_handler",
    ), (
        "scheduler must expose "
        "build_db_bound_wake_execution_handler(db, start_task)"
    )

    handler = scheduler.build_db_bound_wake_execution_handler(
        db,
        fake_start_task,
    )

    plan_item = {
        "task_id": 801,
        "action": "WAKE",
        "state": "READY",
        "reason": "all prerequisites completed",
    }

    before_changes = db.total_changes

    result = handler(plan_item)

    assert_equal(
        result,
        (
            {
                "task_id": 801,
                "project": "sandbox-test-project",
                "changes_file": "/tmp/db-bound-801.json",
                "operation": "python_test",
            },
            0,
        ),
        "DB-bound handler must preserve start_task return contract",
    )
    print("PASS DB-bound handler delegates to start_task")

    assert_equal(
        calls,
        [
            (
                801,
                "sandbox-test-project",
                "/tmp/db-bound-801.json",
                "python_test",
            )
        ],
        "start_task must receive the persisted execution descriptor",
    )
    print("PASS persisted descriptor reaches engine")

    assert_equal(
        db.total_changes,
        before_changes,
        "DB-bound handler must not mutate database state",
    )
    print("PASS DB-bound handler is read-only")

    assert_equal(
        plan_item,
        {
            "task_id": 801,
            "action": "WAKE",
            "state": "READY",
            "reason": "all prerequisites completed",
        },
        "DB-bound handler must not mutate caller plan",
    )
    print("PASS plan item preserved")

    missing_item = {
        "task_id": 802,
        "action": "WAKE",
        "state": "READY",
    }

    try:
        handler(missing_item)
    except ValueError as exc:
        assert "No execution descriptor" in str(exc)
        print("PASS missing descriptor rejected")
    else:
        raise AssertionError(
            "missing execution descriptor must be rejected"
        )

    assert_equal(
        calls,
        [
            (
                801,
                "sandbox-test-project",
                "/tmp/db-bound-801.json",
                "python_test",
            )
        ],
        "missing descriptor must not invoke start_task",
    )
    print("PASS missing descriptor does not reach engine")

    db.close()

    print(
        "WAKE_EXECUTION_HANDLER_DB_BINDING_TEST_COMPLETE"
    )


if __name__ == "__main__":
    main()
