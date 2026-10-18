#!/usr/bin/env python3
"""V0.6.7: expired approvals end the task cleanly."""

import importlib.util
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-approval-service-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("approval_exp", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_db():
    root = Path(tempfile.mkdtemp())
    db_path = root / "brain" / "anu-brain.db"
    db_path.parent.mkdir(parents=True)
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            failure_class TEXT,
            retry_after TEXT
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            goal_id INTEGER,
            event_type TEXT,
            message TEXT,
            metadata TEXT
        );
        """
    )
    db.execute("INSERT INTO tasks (title, status) VALUES ('t', 'pending')")
    db.commit()
    return db


def test_expire_pending():
    m = load()
    db = make_db()
    m.migrate(db)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    # Force short expiry by writing directly after request.
    rec = m.request_approval(
        db, action="research", task_id=1, expiry_hours=24
    )
    db.execute(
        "UPDATE approvals SET expires_at = ? WHERE id = ?",
        (past, rec["id"]),
    )
    db.commit()
    expired_ids = m.expire_pending(db)
    assert rec["id"] in expired_ids
    record = m.get_approval(db, rec["id"])
    assert record["status"] == "expired"
    row = db.execute("SELECT status, failure_class FROM tasks WHERE id = 1").fetchone()
    assert row["status"] == "blocked"
    assert row["failure_class"] == "contract"
    print("PASS expire -> blocked")


def test_approve_after_expire_fails():
    m = load()
    db = make_db()
    m.migrate(db)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    rec = m.request_approval(db, action="browser", task_id=1)
    db.execute(
        "UPDATE approvals SET expires_at = ? WHERE id = ?",
        (past, rec["id"]),
    )
    db.commit()
    m.expire_pending(db)
    try:
        m.approve(db, rec["id"])
        raise AssertionError("expected ApprovalStateError")
    except m.ApprovalStateError:
        pass
    print("PASS approve after expire refused")


def main():
    print("===== ANU V0.6.7 APPROVAL EXPIRY =====")
    test_expire_pending()
    test_approve_after_expire_fails()
    print("APPROVAL_EXPIRY_TEST_COMPLETE")


if __name__ == "__main__":
    main()
