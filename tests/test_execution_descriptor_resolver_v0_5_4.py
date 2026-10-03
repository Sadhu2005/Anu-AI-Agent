#!/usr/bin/env python3

import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"


def load_scheduler():
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_descriptor_test",
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
        (401, "Wake descriptor task"),
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
            401,
            "sandbox-test-project",
            "/tmp/wake-401.json",
            "python_test",
            "2026-10-03T00:00:00Z",
            "2026-10-03T00:00:00Z",
        ),
    )

    db.commit()
    return db


def main():
    print(
        "===== ANU V0.5.4 EXECUTION DESCRIPTOR RESOLVER REGRESSION ====="
    )

    scheduler = load_scheduler()
    db = setup_db()

    try:
        try:
            resolver = scheduler.resolve_execution_descriptor
        except AttributeError:
            print(
                "EXPECTED_RED: resolve_execution_descriptor is not implemented"
            )
            raise

        before_changes = db.total_changes
        before_row = db.execute(
            """
            SELECT task_id, project, changes_file, operation
            FROM task_execution_specs
            WHERE task_id = ?
            """,
            (401,),
        ).fetchone()

        descriptor = resolver(db, 401)

        assert_equal(
            descriptor,
            {
                "task_id": 401,
                "project": "sandbox-test-project",
                "changes_file": "/tmp/wake-401.json",
                "operation": "python_test",
            },
            "resolver must return the persisted execution descriptor",
        )
        print("PASS descriptor resolution")

        after_changes = db.total_changes
        after_row = db.execute(
            """
            SELECT task_id, project, changes_file, operation
            FROM task_execution_specs
            WHERE task_id = ?
            """,
            (401,),
        ).fetchone()

        assert_equal(
            after_changes,
            before_changes,
            "resolver must not mutate the database",
        )
        assert_equal(
            after_row,
            before_row,
            "resolver must preserve execution-spec storage",
        )
        print("PASS resolver is strictly read-only")

        missing = resolver(db, 402)

        assert_equal(
            missing,
            None,
            "missing execution descriptor must return None",
        )
        print("PASS missing descriptor returns None")

        missing_changes = db.execute(
            """
            SELECT COUNT(*)
            FROM task_execution_specs
            WHERE task_id = ?
            """,
            (402,),
        ).fetchone()[0]

        assert_equal(
            missing_changes,
            0,
            "missing resolution must not create a descriptor",
        )
        print("PASS missing descriptor does not create state")

        duplicate_count = db.execute(
            """
            SELECT COUNT(*)
            FROM task_execution_specs
            WHERE task_id = ?
            """,
            (401,),
        ).fetchone()[0]

        assert_equal(
            duplicate_count,
            1,
            "task_id must map to exactly one execution descriptor",
        )
        print("PASS task descriptor cardinality")

        print(
            "EXECUTION_DESCRIPTOR_RESOLVER_TEST_COMPLETE"
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
