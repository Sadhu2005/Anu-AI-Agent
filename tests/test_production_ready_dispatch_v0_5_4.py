#!/usr/bin/env python3

import copy
import importlib.util
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")


def load_scheduler():
    path = BASE / "bin" / "anu-task-scheduler-v0.1.py"
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_production_ready_dispatch_test",
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
            (1301, "ready-high", "Ready High", "", "pending", 10, 0),
            (1302, "ready-low", "Ready Low", "", "pending", 3, 1),
            (1303, "waiting", "Waiting", "", "pending", 8, 0),
            (1304, "blocked", "Blocked prerequisite", "", "blocked", 1, 3),
        ],
    )

    db.execute(
        """
        INSERT INTO task_dependencies(task_id, depends_on_task_id)
        VALUES (?, ?)
        """,
        (1303, 1304),
    )

    db.executemany(
        """
        INSERT INTO task_execution_specs
        (task_id, project, changes_file, operation)
        VALUES (?, ?, ?, ?)
        """,
        [
            (1301, "project-a", "changes-a.json", "apply-a"),
            (1302, "project-b", "changes-b.json", "apply-b"),
        ],
    )

    db.commit()
    return db


def snapshot_db(db):
    return (
        [
            tuple(row)
            for row in db.execute(
                """
                SELECT id, status, priority, attempts
                FROM tasks
                ORDER BY id
                """
            ).fetchall()
        ],
        [
            tuple(row)
            for row in db.execute(
                """
                SELECT task_id, project, changes_file, operation
                FROM task_execution_specs
                ORDER BY task_id
                """
            ).fetchall()
        ],
    )


def test_end_to_end_production_dispatch():
    scheduler = load_scheduler()
    db = build_db()

    calls = []

    class FakeEngine:
        def start_task(self, *args):
            calls.append(copy.deepcopy(args))
            return {
                "ok": True,
                "task_id": args[0],
                "project": args[1],
            }

    scheduler._load_task_engine = lambda: FakeEngine()

    before = snapshot_db(db)

    results = scheduler.dispatch_production_ready_tasks(db)

    after = snapshot_db(db)

    assert calls == [
        (
            1301,
            "project-a",
            "changes-a.json",
            "apply-a",
        ),
        (
            1302,
            "project-b",
            "changes-b.json",
            "apply-b",
        ),
    ]

    assert results == [
        {
            "ok": True,
            "task_id": 1301,
            "project": "project-a",
        },
        {
            "ok": True,
            "task_id": 1302,
            "project": "project-b",
        },
    ]

    assert before == after

    print("PASS end-to-end production dispatch")


def test_waiting_task_never_reaches_engine():
    scheduler = load_scheduler()
    db = build_db()

    calls = []

    class FakeEngine:
        def start_task(self, *args):
            calls.append(args)
            return {"ok": True}

    scheduler._load_task_engine = lambda: FakeEngine()

    # Only the waiting task remains pending.
    db.execute(
        "UPDATE tasks SET status = 'completed' WHERE id IN (1301, 1302)"
    )
    db.commit()

    scheduler.dispatch_production_ready_tasks(db)

    assert calls == []

    print("PASS waiting task never reaches engine")


def test_missing_descriptor_never_reaches_engine():
    scheduler = load_scheduler()
    db = build_db()

    db.execute(
        "DELETE FROM task_execution_specs WHERE task_id = ?",
        (1301,),
    )
    db.commit()

    calls = []

    class FakeEngine:
        def start_task(self, *args):
            calls.append(args)
            return {"ok": True}

    scheduler._load_task_engine = lambda: FakeEngine()

    try:
        scheduler.dispatch_production_ready_tasks(db)
    except ValueError:
        assert calls == []
        print("PASS missing descriptor blocks production dispatch")
        return

    raise AssertionError(
        "Expected missing execution descriptor to block dispatch"
    )


def main():
    print(
        "===== ANU V0.5.4 PRODUCTION READY DISPATCH REGRESSION ====="
    )

    test_end_to_end_production_dispatch()
    test_waiting_task_never_reaches_engine()
    test_missing_descriptor_never_reaches_engine()

    print("PRODUCTION_READY_DISPATCH_TEST_COMPLETE")


if __name__ == "__main__":
    main()
