#!/usr/bin/env python3

import copy
import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")


def load_scheduler():
    path = BASE / "bin" / "anu-task-scheduler-v0.1.py"
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_cycle_test",
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

    db.executemany(
        """
        INSERT INTO tasks
        (id, task_key, title, description, status, priority, attempts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1401, "cycle-high", "Cycle High", "", "pending", 20, 0),
            (1402, "cycle-mid", "Cycle Mid", "", "pending", 10, 0),
            (1403, "cycle-low", "Cycle Low", "", "pending", 5, 0),
            (1404, "cycle-waiting", "Cycle Waiting", "", "pending", 30, 0),
            (1405, "cycle-blocked", "Cycle Blocked", "", "blocked", 40, 3),
        ],
    )

    db.execute(
        """
        INSERT INTO task_dependencies(task_id, depends_on_task_id)
        VALUES (?, ?)
        """,
        (1404, 1405),
    )

    db.executemany(
        """
        INSERT INTO task_execution_specs
        (task_id, project, changes_file, operation)
        VALUES (?, ?, ?, ?)
        """,
        [
            (1401, "project-1401", "changes-1401.json", "apply-1401"),
            (1402, "project-1402", "changes-1402.json", "apply-1402"),
            (1403, "project-1403", "changes-1403.json", "apply-1403"),
        ],
    )

    db.commit()
    return db


def test_cycle_requires_positive_integer_limit():
    scheduler = load_scheduler()
    db = build_db()

    for invalid in (0, -1, True, 1.5, "1", None):
        try:
            scheduler.run_production_scheduler_cycle(db, invalid)
        except (TypeError, ValueError):
            continue

        raise AssertionError(
            f"Expected invalid max_tasks={invalid!r} to be rejected"
        )

    print("PASS scheduler cycle requires positive integer max_tasks")


def test_cycle_dispatches_only_bounded_candidates():
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

    results = scheduler.run_production_scheduler_cycle(db, 2)

    assert calls == [
        (
            1401,
            "project-1401",
            "changes-1401.json",
            "apply-1401",
        ),
        (
            1402,
            "project-1402",
            "changes-1402.json",
            "apply-1402",
        ),
    ]

    assert results == [
        {"ok": True, "task_id": 1401},
        {"ok": True, "task_id": 1402},
    ]

    print("PASS scheduler cycle respects max_tasks")


def test_cycle_preserves_priority_order():
    scheduler = load_scheduler()
    db = build_db()

    calls = []

    class FakeEngine:
        def start_task(self, *args):
            calls.append(args[0])
            return args[0]

    scheduler._load_task_engine = lambda: FakeEngine()

    scheduler.run_production_scheduler_cycle(db, 3)

    assert calls == [1401, 1402, 1403]

    print("PASS scheduler cycle preserves priority order")


def test_cycle_excludes_waiting_and_blocked_tasks():
    scheduler = load_scheduler()
    db = build_db()

    calls = []

    class FakeEngine:
        def start_task(self, *args):
            calls.append(args[0])
            return args[0]

    scheduler._load_task_engine = lambda: FakeEngine()

    scheduler.run_production_scheduler_cycle(db, 10)

    assert 1404 not in calls
    assert 1405 not in calls
    assert calls == [1401, 1402, 1403]

    print("PASS scheduler cycle excludes non-ready tasks")


def test_cycle_with_no_ready_tasks_does_not_execute():
    scheduler = load_scheduler()
    db = build_db()

    db.execute(
        """
        UPDATE tasks
        SET status = 'completed'
        WHERE id IN (1401, 1402, 1403)
        """
    )
    db.commit()

    calls = []

    class FakeEngine:
        def start_task(self, *args):
            calls.append(args)
            return {"ok": True}

    scheduler._load_task_engine = lambda: FakeEngine()

    results = scheduler.run_production_scheduler_cycle(db, 10)

    assert results == []
    assert calls == []

    print("PASS empty scheduler cycle performs no execution")


def main():
    print("===== ANU V0.5.4 PRODUCTION SCHEDULER CYCLE REGRESSION =====")

    test_cycle_requires_positive_integer_limit()
    test_cycle_dispatches_only_bounded_candidates()
    test_cycle_preserves_priority_order()
    test_cycle_excludes_waiting_and_blocked_tasks()
    test_cycle_with_no_ready_tasks_does_not_execute()

    print("PRODUCTION_SCHEDULER_CYCLE_TEST_COMPLETE")


if __name__ == "__main__":
    main()
