#!/usr/bin/env python3

import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")
MODULE = BASE / "bin" / "anu-task-execution-spec-v0.1.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "anu_task_execution_spec_v0_1_test",
        MODULE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load execution-spec module: {MODULE}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            f"{message}\nexpected={expected!r}\nactual={actual!r}"
        )


def main():
    print("===== ANU V0.5.4 EXECUTION SPEC SCHEMA REGRESSION =====")

    module = load_module()

    db = sqlite3.connect(":memory:")

    try:
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
            "INSERT INTO tasks(id, title) VALUES (?, ?)",
            (501, "Execution specification task"),
        )
        db.commit()

        assert hasattr(module, "migrate"), (
            "execution-spec module must expose migrate(db)"
        )

        module.migrate(db)
        print("PASS first migration")

        columns = [
            row[1]
            for row in db.execute(
                "PRAGMA table_info(task_execution_specs)"
            )
        ]

        assert_equal(
            columns,
            [
                "task_id",
                "project",
                "changes_file",
                "operation",
                "created_at",
                "updated_at",
            ],
            "execution-spec schema columns must be canonical",
        )
        print("PASS schema columns")

        fk_rows = db.execute(
            "PRAGMA foreign_key_list(task_execution_specs)"
        ).fetchall()

        assert any(
            row[2] == "tasks" and row[3] == "task_id"
            for row in fk_rows
        ), "task_execution_specs.task_id must reference tasks.id"
        print("PASS task foreign key")

        task_id_columns = [
            row
            for row in db.execute(
                "PRAGMA table_info(task_execution_specs)"
            )
            if row[1] == "task_id"
        ]

        assert len(task_id_columns) == 1, (
            "task_execution_specs must contain exactly one task_id column"
        )

        assert task_id_columns[0][5] == 1, (
            "task_id must be the PRIMARY KEY"
        )

        print("PASS task_id primary key")

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
                501,
                "sandbox-test-project",
                "/tmp/changes-501.json",
                "python_test",
                "2026-10-03T00:00:00Z",
                "2026-10-03T00:00:00Z",
            ),
        )
        db.commit()
        print("PASS valid descriptor insert")

        try:
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
                    9999,
                    "sandbox-test-project",
                    "/tmp/orphan.json",
                    "python_test",
                    "2026-10-03T00:00:00Z",
                    "2026-10-03T00:00:00Z",
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            print("PASS orphan descriptor rejected")
        else:
            raise AssertionError(
                "orphan execution descriptor must be rejected"
            )

        try:
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
                    501,
                    "duplicate-project",
                    "/tmp/duplicate.json",
                    "python_compile",
                    "2026-10-03T00:00:00Z",
                    "2026-10-03T00:00:00Z",
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            print("PASS duplicate task descriptor rejected")
        else:
            raise AssertionError(
                "task_id must uniquely identify one execution descriptor"
            )

        module.migrate(db)
        print("PASS migration idempotency")

        row = db.execute(
            """
            SELECT
                task_id,
                project,
                changes_file,
                operation
            FROM task_execution_specs
            WHERE task_id = ?
            """,
            (501,),
        ).fetchone()

        assert_equal(
            row,
            (
                501,
                "sandbox-test-project",
                "/tmp/changes-501.json",
                "python_test",
            ),
            "existing descriptor must survive repeated migration",
        )
        print("PASS migration preserves descriptor")

        print("EXECUTION_SPEC_SCHEMA_TEST_COMPLETE")

    finally:
        db.close()


if __name__ == "__main__":
    main()
