#!/usr/bin/env python3
"""V0.6.7: approval request parks task; approve resumes to pending."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-approval-service-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("approval_wait", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_db():
    root = Path(tempfile.mkdtemp())
    db_path = root / "brain" / "anu-brain.db"
    db_path.parent.mkdir(parents=True)
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            failure_class TEXT,
            retry_after TEXT
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            goal_id INTEGER,
            event_type TEXT,
            message TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute("INSERT INTO goals (description) VALUES ('g')")
    db.execute(
        "INSERT INTO tasks (goal_id, title, status) VALUES (1, 't', 'pending')"
    )
    db.commit()
    return db


def test_request_puts_task_waiting():
    m = load()
    db = make_db()
    m.migrate(db)
    rec = m.request_approval(
        db,
        action="research",
        task_id=1,
        goal_id=1,
        reason="risky_capability:research",
    )
    assert rec["status"] == "pending"
    assert rec["id"] >= 1
    status = db.execute("SELECT status FROM tasks WHERE id = 1").fetchone()[0]
    assert status == "waiting", status
    print("PASS request -> task waiting")


def test_approve_rewakes_pending():
    m = load()
    db = make_db()
    m.migrate(db)
    rec = m.request_approval(db, action="browser", task_id=1, goal_id=1)
    approved = m.approve(db, rec["id"], decided_by="tester", note="ok")
    assert approved["status"] == "approved"
    row = db.execute(
        "SELECT status, retry_after FROM tasks WHERE id = 1"
    ).fetchone()
    assert row["status"] == "pending", dict(row)
    assert row["retry_after"] is None
    print("PASS approve -> task pending (rewake)")


def test_reject_blocks_task():
    m = load()
    db = make_db()
    m.migrate(db)
    rec = m.request_approval(db, action="filesystem.write", task_id=1)
    rejected = m.reject(db, rec["id"], decided_by="tester", note="no")
    assert rejected["status"] == "rejected"
    row = db.execute(
        "SELECT status, failure_class FROM tasks WHERE id = 1"
    ).fetchone()
    assert row["status"] == "blocked"
    assert row["failure_class"] == "contract"
    print("PASS reject -> task blocked")


def test_prior_approval_allows_gate():
    m = load()
    db = make_db()
    m.migrate(db)
    rec = m.request_approval(db, action="research", task_id=1)
    m.approve(db, rec["id"])
    gated = m.require_autonomous_or_approved(
        db, action="research", task_id=1
    )
    assert gated["classification"] == "AUTONOMOUS"
    assert gated["approval"]["id"] == rec["id"]
    print("PASS prior approval satisfies gate")


def main():
    print("===== ANU V0.6.7 APPROVAL WAIT / REWAKE =====")
    test_request_puts_task_waiting()
    test_approve_rewakes_pending()
    test_reject_blocks_task()
    test_prior_approval_allows_gate()
    print("APPROVAL_WAIT_REWAKE_TEST_COMPLETE")


if __name__ == "__main__":
    main()
