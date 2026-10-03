#!/usr/bin/env python3

import importlib.util
import shutil
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")
SOURCE_DB = BASE / "brain" / "anu-brain.db"
TEST_DB = Path("/tmp/anu-v0.5.3-task-graph-test.db")
MODULE = BASE / "bin" / "anu-task-graph-v0.1.py"


def load_graph():
    spec = importlib.util.spec_from_file_location(
        "anu_task_graph_v0_1",
        MODULE,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    print("===== ANU V0.5.3 TASK GRAPH REGRESSION =====")

    TEST_DB.unlink(missing_ok=True)
    shutil.copy2(SOURCE_DB, TEST_DB)

    graph = load_graph()

    db = sqlite3.connect(TEST_DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    print("PASS foreign keys enabled")

    graph.migrate(db)
    print("PASS dependency schema migration")

    ids = {}

    for title in ("GRAPH-A", "GRAPH-B", "GRAPH-C"):
        cur = db.execute(
            "INSERT INTO tasks(title, status) VALUES (?, ?)",
            (title, "pending"),
        )
        ids[title] = cur.lastrowid

    db.commit()

    a = ids["GRAPH-A"]
    b = ids["GRAPH-B"]
    c = ids["GRAPH-C"]

    graph.add_dependency(db, b, a)
    graph.add_dependency(db, c, b)

    print("PASS dependency chain creation")

    state = graph.dependency_readiness(db, c)
    assert state["state"] == "WAITING"
    print("PASS waiting state")

    db.execute(
        "UPDATE tasks SET status='completed' WHERE id=?",
        (a,),
    )
    db.commit()

    state = graph.dependency_readiness(db, b)
    assert state["state"] == "READY"
    print("PASS prerequisite completion unlock")

    db.execute(
        "UPDATE tasks SET status='completed' WHERE id=?",
        (b,),
    )
    db.commit()

    state = graph.dependency_readiness(db, c)
    assert state["state"] == "READY"
    print("PASS chained readiness")

    try:
        graph.add_dependency(db, c, c)
    except ValueError as exc:
        assert str(exc) == "SELF_DEPENDENCY"
        print("PASS self dependency rejection")
    else:
        raise AssertionError("self dependency accepted")

    try:
        graph.add_dependency(db, a, c)
    except ValueError as exc:
        assert str(exc) == "DEPENDENCY_CYCLE"
        print("PASS cycle rejection")
    else:
        raise AssertionError("cycle accepted")

    try:
        graph.add_dependency(db, c, b)
    except ValueError as exc:
        assert str(exc).startswith(
            "DEPENDENCY_INSERT_REJECTED:"
        )
        print("PASS duplicate rejection")
    else:
        raise AssertionError("duplicate accepted")

    try:
        graph.add_dependency(db, 999999999, a)
    except ValueError as exc:
        assert str(exc).startswith("TASK_NOT_FOUND:")
        print("PASS missing task rejection")
    else:
        raise AssertionError("missing task accepted")

    try:
        graph.add_dependency(db, c, 999999999)
    except ValueError as exc:
        assert str(exc).startswith(
            "PREREQUISITE_TASK_NOT_FOUND:"
        )
        print("PASS missing prerequisite rejection")
    else:
        raise AssertionError("missing prerequisite accepted")

    db.execute(
        "INSERT INTO tasks(title, status, attempts) "
        "VALUES (?, ?, ?)",
        ("GRAPH-FAIL", "failed", 1),
    )
    failed_task = db.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    db.execute(
        "INSERT INTO tasks(title, status) VALUES (?, ?)",
        ("GRAPH-WAIT", "pending"),
    )
    waiting_task = db.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    db.commit()

    graph.add_dependency(db, waiting_task, failed_task)

    state = graph.dependency_readiness(db, waiting_task)
    assert state["state"] == "WAITING"
    print("PASS retryable prerequisite remains waiting")

    db.execute(
        "UPDATE tasks SET attempts=3 WHERE id=?",
        (failed_task,),
    )
    db.commit()

    state = graph.dependency_readiness(db, waiting_task)

    assert state["state"] == "BLOCKED"
    assert (
        state["reason"]
        == "PREREQUISITE_TERMINALLY_UNAVAILABLE"
    )
    print("PASS exhausted prerequisite blocks dependent")

    no_dep = graph.dependency_readiness(db, a)
    assert no_dep["state"] == "READY"
    print("PASS no dependency remains ready")

    removed = graph.remove_dependency(
        db,
        waiting_task,
        failed_task,
    )
    assert removed["removed"] is True
    print("PASS dependency removal")

    db.close()
    TEST_DB.unlink(missing_ok=True)

    print("TASK_GRAPH_V0_5_3_REGRESSION_PASS=1")
    print("===== TASK GRAPH REGRESSION COMPLETE =====")


if __name__ == "__main__":
    main()
