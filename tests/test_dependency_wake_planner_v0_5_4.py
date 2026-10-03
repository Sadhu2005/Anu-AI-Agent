#!/usr/bin/env python3

import importlib.util
import shutil
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")
SOURCE_DB = BASE / "brain" / "anu-brain.db"
TEST_DB = Path("/tmp/anu-v0.5.4-wake-planner-test.db")
GRAPH_MODULE = BASE / "bin" / "anu-task-graph-v0.1.py"
SCHEDULER_MODULE = BASE / "bin" / "anu-task-scheduler-v0.1.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_task(db, title, status="pending", attempts=0):
    cur = db.execute(
        "INSERT INTO tasks(title, status, attempts) VALUES (?, ?, ?)",
        (title, status, attempts),
    )
    return cur.lastrowid


def task_snapshot(db, task_ids):
    placeholders = ",".join("?" for _ in task_ids)

    rows = db.execute(
        f"""
        SELECT id, status, attempts
        FROM tasks
        WHERE id IN ({placeholders})
        ORDER BY id
        """,
        tuple(task_ids),
    ).fetchall()

    return [tuple(row) for row in rows]


def main():
    print("===== ANU V0.5.4 DEPENDENCY WAKE PLANNER REGRESSION =====")

    TEST_DB.unlink(missing_ok=True)
    shutil.copy2(SOURCE_DB, TEST_DB)

    graph = load_module(
        GRAPH_MODULE,
        "anu_task_graph_v0_1_v054_planner_test",
    )

    # Test-first expectation:
    # this module does not exist yet.
    scheduler = load_module(
        SCHEDULER_MODULE,
        "anu_task_scheduler_v0_1_v054",
    )

    db = sqlite3.connect(TEST_DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    graph.migrate(db)

    # ---------------------------------------------------------
    # 1. READY dependent -> WAKE
    # ---------------------------------------------------------
    prereq_ready = create_task(db, "WAKE-A", "completed", 1)
    dependent_ready = create_task(db, "WAKE-B", "pending", 0)
    db.commit()

    graph.add_dependency(db, dependent_ready, prereq_ready)

    plan = scheduler.build_dependency_wake_plan(
        db,
        prereq_ready,
    )

    assert plan == [{
        "task_id": dependent_ready,
        "action": "WAKE",
        "current_status": "pending",
        "current_attempts": 0,
        "state": "READY",
        "reason": "ALL_DEPENDENCIES_COMPLETED",
    }]

    print("PASS READY dependent -> WAKE")

    # ---------------------------------------------------------
    # 2. WAITING dependent -> WAIT
    # ---------------------------------------------------------
    prereq_wait = create_task(db, "WAIT-A", "pending", 0)
    dependent_wait = create_task(db, "WAIT-B", "pending", 0)
    db.commit()

    graph.add_dependency(db, dependent_wait, prereq_wait)

    plan = scheduler.build_dependency_wake_plan(
        db,
        prereq_wait,
    )

    assert plan == [{
        "task_id": dependent_wait,
        "action": "WAIT",
        "current_status": "pending",
        "current_attempts": 0,
        "state": "WAITING",
        "reason": "PREREQUISITES_INCOMPLETE",
    }]

    print("PASS WAITING dependent -> WAIT")

    # ---------------------------------------------------------
    # 3. BLOCKED dependent -> BLOCK
    # ---------------------------------------------------------
    prereq_block = create_task(db, "BLOCK-A", "blocked", 1)
    dependent_block = create_task(db, "BLOCK-B", "pending", 0)
    db.commit()

    graph.add_dependency(db, dependent_block, prereq_block)

    plan = scheduler.build_dependency_wake_plan(
        db,
        prereq_block,
    )

    assert plan == [{
        "task_id": dependent_block,
        "action": "BLOCK",
        "current_status": "pending",
        "current_attempts": 0,
        "state": "BLOCKED",
        "reason": "PREREQUISITE_TERMINALLY_UNAVAILABLE",
    }]

    print("PASS BLOCKED dependent -> BLOCK")

    # ---------------------------------------------------------
    # 4. Deterministic ordering
    # ---------------------------------------------------------
    p = create_task(db, "ORDER-P", "completed", 1)
    d1 = create_task(db, "ORDER-1", "pending", 0)
    d2 = create_task(db, "ORDER-2", "pending", 0)
    d3 = create_task(db, "ORDER-3", "pending", 0)
    db.commit()

    graph.add_dependency(db, d3, p)
    graph.add_dependency(db, d1, p)
    graph.add_dependency(db, d2, p)

    plan = scheduler.build_dependency_wake_plan(db, p)

    assert [item["task_id"] for item in plan] == [d1, d2, d3]
    assert all(item["action"] == "WAKE" for item in plan)

    print("PASS deterministic dependent ordering")

    # ---------------------------------------------------------
    # 5. Planner is strictly read-only
    # ---------------------------------------------------------
    tracked_ids = [
        dependent_ready,
        dependent_wait,
        dependent_block,
        d1,
        d2,
        d3,
    ]

    before = task_snapshot(db, tracked_ids)
    changes_before = db.total_changes

    scheduler.build_dependency_wake_plan(db, prereq_ready)
    scheduler.build_dependency_wake_plan(db, prereq_wait)
    scheduler.build_dependency_wake_plan(db, prereq_block)
    scheduler.build_dependency_wake_plan(db, p)

    after = task_snapshot(db, tracked_ids)

    assert before == after
    assert db.total_changes == changes_before

    print("PASS wake planner is strictly read-only")

    # ---------------------------------------------------------
    # 6. Unknown prerequisite
    # ---------------------------------------------------------
    plan = scheduler.build_dependency_wake_plan(db, 999999999)

    assert plan == []

    print("PASS unknown prerequisite returns empty plan")

    db.close()
    TEST_DB.unlink(missing_ok=True)

    print("DEPENDENCY_WAKE_PLANNER_TEST_COMPLETE")


if __name__ == "__main__":
    main()
