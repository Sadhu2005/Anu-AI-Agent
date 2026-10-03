#!/usr/bin/env python3

import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")
MODULE = BASE / "bin" / "anu-task-execution-spec-v0.1.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "anu_task_execution_spec_v0_1_persistence_test",
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


def setup_db(module):
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
        "INSERT INTO tasks(id, title) VALUES (?, ?)",
        (601, "Persistence task"),
    )
    db.execute(
        "INSERT INTO tasks(id, title) VALUES (?, ?)",
        (602, "Update task"),
    )

    module.migrate(db)
    return db


def main():
    print("===== ANU V0.5.4 EXECUTION SPEC PERSISTENCE REGRESSION =====")

    module = load_module()
    db = setup_db(module)

    try:
        assert hasattr(module, "save_execution_descriptor"), (
            "execution-spec module must expose "
            "save_execution_descriptor(db, task_id, project, changes_file, operation)"
        )

        assert hasattr(module, "fetch_execution_descriptor"), (
            "execution-spec module must expose "
            "fetch_execution_descriptor(db, task_id)"
        )

        result = module.save_execution_descriptor(
            db,
            601,
            "sandbox-test-project",
            "/tmp/changes-601.json",
            "python_test",
        )

        assert_equal(
            result,
            {
                "task_id": 601,
                "project": "sandbox-test-project",
                "changes_file": "/tmp/changes-601.json",
                "operation": "python_test",
            },
            "save must return the canonical descriptor",
        )
        print("PASS descriptor creation")

        fetched = module.fetch_execution_descriptor(db, 601)

        assert_equal(
            fetched,
            result,
            "fetch must return the stored descriptor",
        )
        print("PASS descriptor fetch")

        update_result = module.save_execution_descriptor(
            db,
            601,
            "sandbox-test-project",
            "/tmp/changes-601-r2.json",
            "python_compile",
        )

        assert_equal(
            update_result,
            {
                "task_id": 601,
                "project": "sandbox-test-project",
                "changes_file": "/tmp/changes-601-r2.json",
                "operation": "python_compile",
            },
            "save must update an existing task descriptor",
        )
        print("PASS descriptor update")

        assert_equal(
            module.fetch_execution_descriptor(db, 601),
            update_result,
            "fetch must return the updated descriptor",
        )
        print("PASS updated descriptor fetch")

        assert_equal(
            module.fetch_execution_descriptor(db, 602),
            None,
            "task without descriptor must return None",
        )
        print("PASS missing descriptor")

        invalid_cases = [
            ("missing task", 9999, "project", "changes.json", "python_test"),
            ("empty project", 602, "", "changes.json", "python_test"),
            ("empty changes file", 602, "project", "", "python_test"),
            ("empty operation", 602, "project", "changes.json", ""),
        ]

        for label, task_id, project, changes_file, operation in invalid_cases:
            before = db.execute(
                """
                SELECT task_id, project, changes_file, operation
                FROM task_execution_specs
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()

            try:
                module.save_execution_descriptor(
                    db,
                    task_id,
                    project,
                    changes_file,
                    operation,
                )
            except (ValueError, sqlite3.IntegrityError):
                db.rollback()
                after = db.execute(
                    """
                    SELECT task_id, project, changes_file, operation
                    FROM task_execution_specs
                    WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()

                assert_equal(
                    after,
                    before,
                    f"{label} must not modify persisted state",
                )
                print(f"PASS invalid input rejected: {label}")
            else:
                raise AssertionError(
                    f"{label} must be rejected"
                )

        print("EXECUTION_SPEC_PERSISTENCE_TEST_COMPLETE")

    finally:
        db.close()


if __name__ == "__main__":
    main()
