#!/usr/bin/env python3
"""V0.6.8: durable wake conditions survive and evaluate after restart."""

import importlib.util
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-startup-recovery-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("wake_t", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_db():
    root = Path(tempfile.mkdtemp())
    db_path = root / "anu-brain.db"
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting',
            retry_after TEXT
        );
        CREATE TABLE approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        """
    )
    db.execute("INSERT INTO tasks (title, status) VALUES ('t', 'waiting')")
    db.commit()
    return db


def test_time_wake_resumes_waiting_task():
    m = load()
    db = make_db()
    m.migrate(db)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    wid = m.register_wake(
        db, wake_kind=m.WAKE_TIME, task_id=1, wake_at=past
    )
    result = m.evaluate_wake_conditions(db)
    assert wid in result["satisfied"]
    status = db.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0]
    assert status == "pending", status
    print("PASS time wake resumes waiting task")


def test_approval_wake_after_grant():
    m = load()
    db = make_db()
    m.migrate(db)
    m.register_wake(db, wake_kind=m.WAKE_APPROVAL, task_id=1)
    # Still pending approval → not due.
    result = m.evaluate_wake_conditions(db)
    assert result["satisfied"] == []
    db.execute(
        "INSERT INTO approvals (task_id, action, status) VALUES (1, 'research', 'approved')"
    )
    db.commit()
    result2 = m.evaluate_wake_conditions(db)
    assert result2["satisfied"]
    status = db.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0]
    assert status == "pending"
    print("PASS approval wake after grant")


def test_future_time_wake_holds():
    m = load()
    db = make_db()
    m.migrate(db)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    m.register_wake(db, wake_kind=m.WAKE_TIME, task_id=1, wake_at=future)
    result = m.evaluate_wake_conditions(db)
    assert result["satisfied"] == []
    status = db.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0]
    assert status == "waiting"
    print("PASS future time wake holds")


def main():
    print("===== ANU V0.6.8 DURABLE WAKE CONDITIONS =====")
    test_time_wake_resumes_waiting_task()
    test_approval_wake_after_grant()
    test_future_time_wake_holds()
    print("DURABLE_WAKE_CONDITIONS_TEST_COMPLETE")


if __name__ == "__main__":
    main()
