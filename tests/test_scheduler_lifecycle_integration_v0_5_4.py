#!/usr/bin/env python3

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


BASE = Path("/srv/anu")
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"


def load_scheduler():
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_lifecycle_integration_test",
        SCHEDULER,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load scheduler: {SCHEDULER}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_scheduler_delegates_through_lifecycle():
    scheduler = load_scheduler()

    assert hasattr(
        scheduler,
        "run_scheduler_cycle_once",
    ), "scheduler must expose run_scheduler_cycle_once()"

    observed = {}

    def fake_production_cycle(db, max_tasks):
        assert isinstance(db, sqlite3.Connection)
        observed["db"] = db
        observed["max_tasks"] = max_tasks

        return {
            "status": "FAKE_PRODUCTION_CYCLE",
            "max_tasks": max_tasks,
        }

    original = scheduler.run_production_scheduler_cycle
    scheduler.run_production_scheduler_cycle = fake_production_cycle

    try:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "brain.db"
            lock_path = Path(td) / "scheduler.lock"

            result = scheduler.run_scheduler_cycle_once(
                db_path=db_path,
                max_tasks=4,
                lock_path=lock_path,
            )

            assert result == {
                "status": "FAKE_PRODUCTION_CYCLE",
                "max_tasks": 4,
            }

            assert observed["max_tasks"] == 4
            assert isinstance(observed["db"], sqlite3.Connection)
            assert db_path.exists()
            assert lock_path.exists()

    finally:
        scheduler.run_production_scheduler_cycle = original

    print("PASS production scheduler delegates through lifecycle")


def test_scheduler_loads_lifecycle_runtime():
    scheduler = load_scheduler()

    lifecycle = scheduler._load_lifecycle()

    assert callable(
        lifecycle.run_scheduler_cycle_once
    ), "lifecycle must expose run_scheduler_cycle_once()"

    print("PASS scheduler loads lifecycle runtime module")


if __name__ == "__main__":
    print("===== ANU V0.5.4 SCHEDULER LIFECYCLE INTEGRATION =====")

    test_production_scheduler_delegates_through_lifecycle()
    test_scheduler_loads_lifecycle_runtime()

    print("SCHEDULER_LIFECYCLE_INTEGRATION_TEST_COMPLETE")
