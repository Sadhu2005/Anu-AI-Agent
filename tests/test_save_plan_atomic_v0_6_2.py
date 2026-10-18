#!/usr/bin/env python3
"""F5: save_plan must not leave zero active plans on failure or races."""

import importlib.util
import sqlite3
import tempfile
import threading
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
PLAN_MODULE = REPO / "bin" / "anu-goal-plan-service-v0.1.py"
GOAL_MODULE = REPO / "bin" / "anu-goal-service-v0.1.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if path == PLAN_MODULE:
        mod.MODEL_MODULE = REPO / "bin" / "anu-agent-model-v0.1.py"
    return mod


def make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            goal_id INTEGER,
            parent_task_id INTEGER,
            run_id TEXT,
            attempt INTEGER,
            event_type TEXT NOT NULL,
            message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        "INSERT INTO goals(description, status) VALUES ('atomic plan test', 'pending')"
    )
    db.commit()
    return path, db


def sample_plan(plan_id, action_id="a1"):
    return {
        "plan_id": plan_id,
        "goal_id": "1",
        "actions": [
            {
                "action_id": action_id,
                "goal_id": "1",
                "kind": "TASK",
                "description": "step",
                "parameters": {"step": "inspect"},
            }
        ],
    }


class DbProxy:
    """Delegate to sqlite3.Connection; optional hook on execute()."""

    def __init__(self, db, before_execute=None):
        self._db = db
        self._before_execute = before_execute

    def execute(self, sql, parameters=()):
        if self._before_execute is not None:
            self._before_execute(sql, parameters)
        return self._db.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self._db, name)


def count_active(db, goal_id=1):
    row = db.execute(
        "SELECT COUNT(*) AS n FROM goal_plans "
        "WHERE goal_id = ? AND status = 'active'",
        (goal_id,),
    ).fetchone()
    return int(row["n"])


def test_failure_after_supersede_keeps_one_active():
    ps = load(PLAN_MODULE, "plan_atomic_v062")
    path, db = make_db()
    try:
        ps.migrate(db)
        ps.save_plan(db, 1, sample_plan("plan-v1", "a1"))
        assert count_active(db) == 1

        def fail_on_insert(sql, parameters=()):
            if isinstance(sql, str) and "INSERT INTO goal_plans" in sql:
                raise sqlite3.OperationalError("simulated mid-save failure")

        proxy = DbProxy(db, before_execute=fail_on_insert)
        try:
            ps.save_plan(proxy, 1, sample_plan("plan-v2", "a2"))
            raise AssertionError("save_plan should have failed")
        except sqlite3.OperationalError:
            pass

        assert count_active(db) == 1
        assert ps.get_active_plan(db, 1)["plan_id"] == "plan-v1"
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS failure after supersede keeps one active plan")


def test_sequential_saves_one_active_version_increments():
    ps = load(PLAN_MODULE, "plan_seq_v062")
    path, db = make_db()
    try:
        ps.migrate(db)
        v1 = ps.save_plan(db, 1, sample_plan("plan-s1", "a1"))
        v2 = ps.save_plan(db, 1, sample_plan("plan-s2", "a2"))
        assert v1["version"] == 1
        assert v2["version"] == 2
        assert count_active(db) == 1
        assert ps.get_active_plan(db, 1)["plan_id"] == "plan-s2"
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS sequential saves leave one active with incremented version")


def test_concurrent_saves_one_active():
    ps = load(PLAN_MODULE, "plan_conc_v062")
    path, seed = make_db()
    seed.close()
    setup = ps.db_connect(path)
    try:
        ps.migrate(setup)
    finally:
        setup.close()

    results = {"errors": [], "ok": 0}
    lock = threading.Lock()

    def worker(plan_id, action_id):
        conn = ps.db_connect(path)
        try:
            ps.migrate(conn)
            ps.save_plan(conn, 1, sample_plan(plan_id, action_id))
            with lock:
                results["ok"] += 1
        except Exception as exc:
            with lock:
                results["errors"].append(str(exc))
        finally:
            conn.close()

    t1 = threading.Thread(target=worker, args=("plan-c1", "c1"))
    t2 = threading.Thread(target=worker, args=("plan-c2", "c2"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    db = ps.db_connect(path)
    try:
        assert count_active(db) == 1
        assert results["ok"] >= 1
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS concurrent saves end with exactly one active plan")


if __name__ == "__main__":
    load(GOAL_MODULE, "goal_atomic_v062")
    print("===== ANU F5 SAVE_PLAN ATOMIC REGRESSION =====")
    test_failure_after_supersede_keeps_one_active()
    test_sequential_saves_one_active_version_increments()
    test_concurrent_saves_one_active()
    print("SAVE_PLAN_ATOMIC_TEST_COMPLETE")
