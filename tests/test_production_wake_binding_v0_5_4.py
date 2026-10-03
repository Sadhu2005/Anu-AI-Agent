#!/usr/bin/env python3

import copy
import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")


def load_scheduler():
    path = BASE / "bin" / "anu-task-scheduler-v0.1.py"
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_production_binding_test",
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

        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE
        );
        """
    )

    db.execute(
        """
        INSERT INTO tasks
        (id, task_key, title, description, status, priority, attempts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (1201, "production-bind", "Production binding", "", "pending", 10, 0),
    )

    db.execute(
        """
        INSERT INTO task_execution_specs
        (task_id, project, changes_file, operation)
        VALUES (?, ?, ?, ?)
        """,
        (
            1201,
            "demo-project",
            "changes.json",
            "apply mutation plan",
        ),
    )

    db.commit()
    return db


def test_factory_constructs_without_execution():
    scheduler = load_scheduler()
    db = build_db()

    calls = []

    class FakeEngine:
        def start_task(self, *args):
            calls.append(args)
            return {"ok": True}

    scheduler._load_task_engine = lambda: FakeEngine()

    handler = scheduler.build_production_wake_execution_handler(db)

    assert callable(handler)
    assert calls == []

    print("PASS production binding constructs without execution")


def test_valid_wake_reaches_engine_start_task():
    scheduler = load_scheduler()
    db = build_db()

    calls = []

    class FakeEngine:
        def start_task(self, *args):
            calls.append(copy.deepcopy(args))
            return {
                "ok": True,
                "task_id": args[0],
            }

    scheduler._load_task_engine = lambda: FakeEngine()

    handler = scheduler.build_production_wake_execution_handler(db)

    plan_item = {
        "task_id": 1201,
        "action": "WAKE",
        "current_status": "pending",
        "current_attempts": 0,
        "state": "READY",
    }

    original = copy.deepcopy(plan_item)

    result = handler(plan_item)

    assert result == {
        "ok": True,
        "task_id": 1201,
    }

    assert calls == [
        (
            1201,
            "demo-project",
            "changes.json",
            "apply mutation plan",
        )
    ]

    assert plan_item == original

    print("PASS valid WAKE reaches engine start_task")


def test_missing_descriptor_never_reaches_engine():
    scheduler = load_scheduler()

    db = build_db()
    db.execute(
        "DELETE FROM task_execution_specs WHERE task_id = ?",
        (1201,),
    )
    db.commit()

    calls = []

    class FakeEngine:
        def start_task(self, *args):
            calls.append(args)
            return {"ok": True}

    scheduler._load_task_engine = lambda: FakeEngine()

    handler = scheduler.build_production_wake_execution_handler(db)

    plan_item = {
        "task_id": 1201,
        "action": "WAKE",
        "current_status": "pending",
        "current_attempts": 0,
        "state": "READY",
    }

    try:
        handler(plan_item)
    except ValueError:
        assert calls == []
        print("PASS missing descriptor blocks engine")
        return

    raise AssertionError(
        "Expected missing descriptor to raise ValueError"
    )


def test_non_callable_engine_start_task_rejected():
    scheduler = load_scheduler()
    db = build_db()

    class InvalidEngine:
        start_task = None

    scheduler._load_task_engine = lambda: InvalidEngine()

    try:
        scheduler.build_production_wake_execution_handler(db)
    except (TypeError, RuntimeError):
        print("PASS invalid engine start_task rejected")
        return

    raise AssertionError(
        "Expected invalid engine start_task to be rejected"
    )


def main():
    print("===== ANU V0.5.4 PRODUCTION WAKE BINDING REGRESSION =====")

    test_factory_constructs_without_execution()
    test_valid_wake_reaches_engine_start_task()
    test_missing_descriptor_never_reaches_engine()
    test_non_callable_engine_start_task_rejected()

    print("PRODUCTION_WAKE_BINDING_TEST_COMPLETE")


if __name__ == "__main__":
    main()
