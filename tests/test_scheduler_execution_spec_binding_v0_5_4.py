#!/usr/bin/env python3

import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"


def load_scheduler():
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_binding_test",
        SCHEDULER,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load scheduler: {SCHEDULER}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TrapDB:
    """DB double that fails if scheduler performs SQL itself."""

    def execute(self, *args, **kwargs):
        raise AssertionError(
            "scheduler must delegate descriptor reads to "
            "the execution-spec module"
        )


class FakeExecutionSpec:
    def __init__(self):
        self.calls = []

    def fetch_execution_descriptor(self, db, task_id):
        self.calls.append((db, task_id))
        return {
            "task_id": task_id,
            "project": "delegated-project",
            "changes_file": "/tmp/delegated.json",
            "operation": "python_test",
        }


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            f"{message}\nexpected={expected!r}\nactual={actual!r}"
        )


def main():
    print(
        "===== ANU V0.5.4 SCHEDULER EXECUTION-SPEC BINDING REGRESSION ====="
    )

    scheduler = load_scheduler()
    fake_module = FakeExecutionSpec()

    assert hasattr(scheduler, "_load_execution_spec"), (
        "scheduler must expose _load_execution_spec()"
    )

    original_loader = scheduler._load_execution_spec
    scheduler._load_execution_spec = lambda: fake_module

    try:
        db = TrapDB()

        descriptor = scheduler.resolve_execution_descriptor(db, 701)

        assert_equal(
            descriptor,
            {
                "task_id": 701,
                "project": "delegated-project",
                "changes_file": "/tmp/delegated.json",
                "operation": "python_test",
            },
            "scheduler resolver must delegate to execution-spec fetch API",
        )
        print("PASS scheduler delegates descriptor read")

        assert_equal(
            len(fake_module.calls),
            1,
            "execution-spec fetch must be called exactly once",
        )
        assert_equal(
            fake_module.calls[0][0],
            db,
            "scheduler must pass through the database handle",
        )
        assert_equal(
            fake_module.calls[0][1],
            701,
            "scheduler must pass through task_id",
        )
        print("PASS descriptor delegation arguments")

    finally:
        scheduler._load_execution_spec = original_loader

    print("SCHEDULER_EXECUTION_SPEC_BINDING_TEST_COMPLETE")


if __name__ == "__main__":
    main()
