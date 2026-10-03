#!/usr/bin/env python3

import importlib.util
import shutil
import sqlite3
from pathlib import Path

BASE = Path("/srv/anu")
SOURCE_DB = BASE / "brain" / "anu-brain.db"
TEST_DB = Path("/tmp/anu-v0.5.4-task-graph-test.db")
MODULE = BASE / "bin" / "anu-task-graph-v0.1.py"


def load_graph():
    spec = importlib.util.spec_from_file_location(
        "anu_task_graph_v0_1_v054",
        MODULE,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Task Graph module")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def task_status(db, task_id):
    row = db.execute(
        "SELECT status, attempts FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    return tuple(row)


def create_task(db, title, status="pending", attempts=0):
    cur = db.execute(
        "INSERT INTO tasks(title, status, attempts) VALUES (?, ?, ?)",
        (title, status, attempts),
    )
    return cur.lastrowid


def readiness(graph, db, task_id):
    state = graph.dependency_readiness(db, task_id)
    print(
        f"READINESS task={task_id} "
        f"state={state['state']} "
        f"reason={state['reason']}"
    )
    return state


def main():
    print("===== ANU V0.5.4 TASK GRAPH TRANSITION REGRESSION =====")

    TEST_DB.unlink(missing_ok=True)
    shutil.copy2(SOURCE_DB, TEST_DB)

    graph = load_graph()

    db = sqlite3.connect(TEST_DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    graph.migrate(db)
    print("PASS isolated database and schema")

    # ---------------------------------------------------------
    # 1. WAITING -> READY after prerequisite completion
    # ---------------------------------------------------------
    prerequisite = create_task(db, "TRANS-A", "pending", 0)
    dependent = create_task(db, "TRANS-B", "pending", 0)
    db.commit()

    graph.add_dependency(db, dependent, prerequisite)

    state = readiness(graph, db, dependent)
    assert state["state"] == "WAITING"

    before = task_status(db, dependent)

    db.execute(
        "UPDATE tasks SET status='completed' WHERE id=?",
        (prerequisite,),
    )
    db.commit()

    state = readiness(graph, db, dependent)
    assert state["state"] == "READY"

    after = task_status(db, dependent)

    assert before == after
    assert after == ("pending", 0)

    print("PASS WAITING -> READY after prerequisite completion")
    print("PASS readiness evaluation does not mutate dependent task")

    # ---------------------------------------------------------
    # 2. Retryable failed prerequisite -> READY after recovery
    # ---------------------------------------------------------
    retry_prereq = create_task(db, "RETRY-A", "failed", 1)
    retry_dependent = create_task(db, "RETRY-B", "pending", 0)
    db.commit()

    graph.add_dependency(db, retry_dependent, retry_prereq)

    state = readiness(graph, db, retry_dependent)
    assert state["state"] == "WAITING"
    assert state["reason"] == "PREREQUISITES_INCOMPLETE"

    db.execute(
        "UPDATE tasks SET status='completed' WHERE id=?",
        (retry_prereq,),
    )
    db.commit()

    state = readiness(graph, db, retry_dependent)
    assert state["state"] == "READY"

    print("PASS retryable prerequisite WAITING -> READY after recovery")

    # ---------------------------------------------------------
    # 3. Exhausted failed prerequisite -> BLOCKED
    # ---------------------------------------------------------
    exhausted_prereq = create_task(db, "EXHAUST-A", "failed", 3)
    exhausted_dependent = create_task(db, "EXHAUST-B", "pending", 0)
    db.commit()

    graph.add_dependency(db, exhausted_dependent, exhausted_prereq)

    state = readiness(graph, db, exhausted_dependent)

    assert state["state"] == "BLOCKED"
    assert state["reason"] == "PREREQUISITE_TERMINALLY_UNAVAILABLE"

    print("PASS exhausted prerequisite WAITING path resolves to BLOCKED")

    # ---------------------------------------------------------
    # 4. Multiple dependencies: partial completion stays WAITING
    # ---------------------------------------------------------
    multi_a = create_task(db, "MULTI-A", "completed", 1)
    multi_b = create_task(db, "MULTI-B", "pending", 0)
    multi_c = create_task(db, "MULTI-C", "pending", 0)
    db.commit()

    graph.add_dependency(db, multi_c, multi_a)
    graph.add_dependency(db, multi_c, multi_b)

    state = readiness(graph, db, multi_c)
    assert state["state"] == "WAITING"

    assert len(state["waiting"]) == 1
    assert state["waiting"][0]["task_id"] == multi_b

    db.execute(
        "UPDATE tasks SET status='completed' WHERE id=?",
        (multi_b,),
    )
    db.commit()

    state = readiness(graph, db, multi_c)
    assert state["state"] == "READY"
    assert state["waiting"] == []
    assert state["blocked"] == []

    print("PASS multi-dependency partial completion stays WAITING")
    print("PASS multi-dependency completion resolves to READY")

    # ---------------------------------------------------------
    # 5. A blocked prerequisite dominates other completed deps
    # ---------------------------------------------------------
    blocked_a = create_task(db, "BLOCK-A", "completed", 1)
    blocked_b = create_task(db, "BLOCK-B", "blocked", 1)
    blocked_c = create_task(db, "BLOCK-C", "pending", 0)
    db.commit()

    graph.add_dependency(db, blocked_c, blocked_a)
    graph.add_dependency(db, blocked_c, blocked_b)

    state = readiness(graph, db, blocked_c)

    assert state["state"] == "BLOCKED"
    assert state["reason"] == "PREREQUISITE_TERMINALLY_UNAVAILABLE"
    assert any(
        item["task_id"] == blocked_b
        and item["reason"] == "PREREQUISITE_BLOCKED"
        for item in state["blocked"]
    )

    print("PASS blocked prerequisite dominates completed dependency")

    # ---------------------------------------------------------
    # 6. WAITING -> READY after dependency removal
    # ---------------------------------------------------------
    remove_a = create_task(db, "REMOVE-A", "pending", 0)
    remove_b = create_task(db, "REMOVE-B", "pending", 0)
    db.commit()

    graph.add_dependency(db, remove_b, remove_a)

    state = readiness(graph, db, remove_b)
    assert state["state"] == "WAITING"

    removed = graph.remove_dependency(db, remove_b, remove_a)
    assert removed["removed"] is True

    state = readiness(graph, db, remove_b)
    assert state["state"] == "READY"
    assert state["reason"] == "NO_DEPENDENCIES"

    print("PASS dependency removal WAITING -> READY")

    # ---------------------------------------------------------
    # 7. Readiness checks never mutate attempts/status
    # ---------------------------------------------------------
    snapshot_before = {
        dependent: task_status(db, dependent),
        retry_dependent: task_status(db, retry_dependent),
        exhausted_dependent: task_status(db, exhausted_dependent),
        multi_c: task_status(db, multi_c),
        blocked_c: task_status(db, blocked_c),
        remove_b: task_status(db, remove_b),
    }

    for task_id in snapshot_before:
        readiness(graph, db, task_id)

    snapshot_after = {
        task_id: task_status(db, task_id)
        for task_id in snapshot_before
    }

    assert snapshot_before == snapshot_after

    print("PASS repeated readiness evaluation preserves status/attempts")

    # ---------------------------------------------------------
    # 8. Dependent discovery contract
    # ---------------------------------------------------------
    completed_prereq = create_task(db, "DISCOVER-A", "completed", 1)
    ready_dependent = create_task(db, "DISCOVER-B", "pending", 0)
    waiting_dependent = create_task(db, "DISCOVER-C", "pending", 0)
    blocked_prereq = create_task(db, "DISCOVER-D", "blocked", 1)
    blocked_dependent = create_task(db, "DISCOVER-E", "pending", 0)
    db.commit()

    graph.add_dependency(db, ready_dependent, completed_prereq)
    graph.add_dependency(db, waiting_dependent, dependent)
    graph.add_dependency(db, blocked_dependent, blocked_prereq)

    discovered = graph.list_dependents(
        db,
        completed_prereq,
    )

    assert len(discovered) == 1
    assert discovered[0]["task_id"] == ready_dependent
    assert discovered[0]["dependent_status"] == "pending"
    assert discovered[0]["dependent_attempts"] == 0
    state = readiness(graph, db, ready_dependent)
    assert state["state"] == "READY"

    state = readiness(graph, db, blocked_dependent)
    assert state["state"] == "BLOCKED"

    assert task_status(db, ready_dependent) == ("pending", 0)
    assert task_status(db, blocked_dependent) == ("pending", 0)

    print("PASS dependent discovery does not mutate task state")
    print("PASS discovered dependents retain pending status")

    # ---------------------------------------------------------
    # 9. Dependent discovery: ordering, empty result, read-only
    # ---------------------------------------------------------
    second_ready_dependent = create_task(
        db,
        "DISCOVER-F",
        "pending",
        0,
    )
    third_ready_dependent = create_task(
        db,
        "DISCOVER-G",
        "pending",
        0,
    )
    db.commit()

    graph.add_dependency(db, third_ready_dependent, completed_prereq)
    graph.add_dependency(db, second_ready_dependent, completed_prereq)

    changes_before = db.total_changes

    discovered = graph.list_dependents(
        db,
        completed_prereq,
    )

    changes_after = db.total_changes

    ids = [item["task_id"] for item in discovered]

    assert ids == sorted(ids)
    assert ids == [
        ready_dependent,
        second_ready_dependent,
        third_ready_dependent,
    ]
    assert all(
        item["dependent_status"] == "pending"
        for item in discovered
    )
    assert all(
        item["dependent_attempts"] == 0
        for item in discovered
    )
    assert changes_after == changes_before

    assert graph.list_dependents(
        db,
        999999999,
    ) == []

    assert task_status(db, ready_dependent) == ("pending", 0)
    assert task_status(db, second_ready_dependent) == ("pending", 0)
    assert task_status(db, third_ready_dependent) == ("pending", 0)

    print("PASS dependent discovery ordering is deterministic")
    print("PASS unknown prerequisite returns empty dependent set")
    print("PASS dependent discovery is strictly read-only")

    # ---------------------------------------------------------
    # 10. Batch dependent readiness evaluation
    # ---------------------------------------------------------
    batch_prereq = create_task(
        db,
        "BATCH-A",
        "completed",
        1,
    )

    batch_ready = create_task(
        db,
        "BATCH-B",
        "pending",
        0,
    )

    batch_waiting_prereq = create_task(
        db,
        "BATCH-C",
        "pending",
        0,
    )

    batch_waiting = create_task(
        db,
        "BATCH-D",
        "pending",
        0,
    )

    batch_blocked_prereq = create_task(
        db,
        "BATCH-E",
        "blocked",
        1,
    )

    batch_blocked = create_task(
        db,
        "BATCH-F",
        "pending",
        0,
    )

    db.commit()

    graph.add_dependency(db, batch_ready, batch_prereq)

    graph.add_dependency(db, batch_waiting, batch_prereq)
    graph.add_dependency(db, batch_waiting, batch_waiting_prereq)

    graph.add_dependency(db, batch_blocked, batch_prereq)
    graph.add_dependency(db, batch_blocked, batch_blocked_prereq)

    before_states = {
        task_id: task_status(db, task_id)
        for task_id in (
            batch_ready,
            batch_waiting,
            batch_blocked,
        )
    }

    before_changes = db.total_changes

    evaluations = graph.evaluate_dependents(
        db,
        batch_prereq,
    )

    after_changes = db.total_changes

    assert [item["task_id"] for item in evaluations] == [
        batch_ready,
        batch_waiting,
        batch_blocked,
    ]

    by_id = {
        item["task_id"]: item
        for item in evaluations
    }

    assert by_id[batch_ready]["state"] == "READY"
    assert by_id[batch_ready]["reason"] == "ALL_DEPENDENCIES_COMPLETED"

    assert by_id[batch_waiting]["state"] == "WAITING"
    assert by_id[batch_waiting]["reason"] == "PREREQUISITES_INCOMPLETE"

    assert by_id[batch_blocked]["state"] == "BLOCKED"
    assert by_id[batch_blocked]["reason"] == "PREREQUISITE_TERMINALLY_UNAVAILABLE"

    assert by_id[batch_ready]["current_status"] == "pending"
    assert by_id[batch_waiting]["current_status"] == "pending"
    assert by_id[batch_blocked]["current_status"] == "pending"

    assert before_states == {
        task_id: task_status(db, task_id)
        for task_id in (
            batch_ready,
            batch_waiting,
            batch_blocked,
        )
    }

    assert after_changes == before_changes

    assert graph.evaluate_dependents(
        db,
        999999999,
    ) == []

    print("PASS batch dependent evaluation returns deterministic readiness")
    print("PASS batch dependent evaluation preserves task state")
    print("PASS batch dependent evaluation is strictly read-only")




    db.close()
    TEST_DB.unlink(missing_ok=True)

    print("TASK_GRAPH_V0_5_4_TRANSITION_REGRESSION_PASS=1")
    print("===== TASK GRAPH V0.5.4 REGRESSION COMPLETE =====")


if __name__ == "__main__":
    main()
