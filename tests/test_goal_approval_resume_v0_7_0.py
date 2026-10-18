#!/usr/bin/env python3
"""V0.7.0: approval pause + wake re-eval resumes task without reboot."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
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
        "INSERT INTO tasks (goal_id, title, status) VALUES (1, 'risky', 'pending')"
    )
    db.commit()
    return path, db


def test_wake_reeval_resumes_after_approval_decision():
    """Prove mid-loop wake path: approved + waiting → pending via evaluate."""
    approval = load(
        REPO / "bin" / "anu-approval-service-v0.1.py",
        "approval_v070",
    )
    startup = load(
        REPO / "bin" / "anu-startup-recovery-v0.1.py",
        "startup_v070",
    )
    path, db = make_db()
    try:
        approval.migrate(db)
        startup.migrate(db)

        rec = approval.request_approval(
            db,
            action="research",
            task_id=1,
            goal_id=1,
            reason="risky_capability:research",
        )
        assert db.execute(
            "SELECT status FROM tasks WHERE id = 1"
        ).fetchone()[0] == "waiting"

        wakes_pending = db.execute(
            "SELECT COUNT(*) FROM wake_conditions WHERE status = 'pending'"
        ).fetchone()[0]
        assert wakes_pending >= 1, "approval should register a wake"

        # Decision recorded without using approve()'s direct task resume,
        # so wake re-eval is the resume path (mid-loop / post-reboot).
        db.execute(
            "UPDATE approvals SET status = 'approved' WHERE id = ?",
            (rec["id"],),
        )
        db.commit()
        assert db.execute(
            "SELECT status FROM tasks WHERE id = 1"
        ).fetchone()[0] == "waiting"

        wakes = startup.evaluate_wake_conditions(db)
        assert wakes["checked"] >= 1
        assert len(wakes["satisfied"]) >= 1
        status = db.execute(
            "SELECT status FROM tasks WHERE id = 1"
        ).fetchone()[0]
        assert status == "pending", status
    finally:
        db.close()
        path.unlink(missing_ok=True)
    print("PASS wake re-eval resumes waiting task after approval")


if __name__ == "__main__":
    print("===== ANU V0.7.0 GOAL APPROVAL RESUME =====")
    test_wake_reeval_resumes_after_approval_decision()
    print("GOAL_APPROVAL_RESUME_TEST_COMPLETE")
