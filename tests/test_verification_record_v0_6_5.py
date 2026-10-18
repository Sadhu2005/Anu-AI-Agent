#!/usr/bin/env python3
"""V0.6.5: verification record + ACCEPT/RETRY/ROLLBACK decisions."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-verification-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("verification_t", MOD)
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
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            repository TEXT NOT NULL,
            workspace TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    db.execute(
        "INSERT INTO projects (project_key, name, repository, workspace) "
        "VALUES ('sandbox', 'Sandbox', 'local', '/tmp/sandbox')"
    )
    db.execute(
        "INSERT INTO goals (project_id, description) VALUES (1, 'g')"
    )
    db.execute(
        "INSERT INTO tasks (goal_id, title, status, attempts) "
        "VALUES (1, 't1', 'verifying', 1)"
    )
    db.commit()
    return root, db_path, db


def test_accept_on_committed():
    m = load()
    _root, _db_path, db = make_db()
    m.migrate(db)
    record = m.evaluate_mutation_attempt(
        db,
        {
            "status": "COMMITTED",
            "transaction_id": "mut-1",
            "operation": "python_test",
            "files": ["app.py"],
            "mutation_test_ok": True,
            "integrity_verified": True,
            "baseline_ok": True,
            "file_records": [
                {
                    "path": "app.py",
                    "baseline_sha256": "aaa",
                    "after_sha256": "bbb",
                }
            ],
            "actual_changed_files": ["app.py"],
        },
        task_id=1,
        attempt=1,
        goal_id=1,
        run_id="run-1",
    )
    assert record["decision"] == "ACCEPT", record
    assert record["tests_ok"] is True
    assert record["intended_ok"] is True
    assert record["unintended_ok"] is True
    assert record["id"] >= 1
    latest = m.get_latest_for_task(db, 1)
    assert latest["id"] == record["id"]
    m.require_accept_for_complete(db, 1, attempt=1)
    print("PASS accept on COMMITTED + digests")


def test_retry_on_rolled_back():
    m = load()
    _root, _db_path, db = make_db()
    m.migrate(db)
    record = m.evaluate_mutation_attempt(
        db,
        {
            "status": "ROLLED_BACK",
            "operation": "python_test",
            "files": ["app.py"],
            "mutation_test_ok": False,
            "rollback_attempted": True,
            "rollback_verified": True,
            "integrity_verified": True,
        },
        task_id=1,
        attempt=1,
    )
    assert record["decision"] == "RETRY", record
    outcome = m.decision_to_task_outcome(record["decision"])
    assert outcome["task_status"] == "failed"
    assert outcome["failure_class"] == "code_defect"
    print("PASS RETRY on ROLLED_BACK")


def test_complete_requires_accept():
    m = load()
    _root, _db_path, db = make_db()
    m.migrate(db)
    m.evaluate_mutation_attempt(
        db,
        {
            "status": "ROLLED_BACK",
            "files": ["app.py"],
            "mutation_test_ok": False,
            "rollback_attempted": True,
            "rollback_verified": True,
            "integrity_verified": True,
        },
        task_id=1,
        attempt=1,
    )
    try:
        m.require_accept_for_complete(db, 1)
        raise AssertionError("expected VerificationError")
    except m.VerificationError:
        pass
    print("PASS COMPLETE refused without ACCEPT")


def main():
    print("===== ANU V0.6.5 VERIFICATION RECORD =====")
    test_accept_on_committed()
    test_retry_on_rolled_back()
    test_complete_requires_accept()
    print("VERIFICATION_RECORD_TEST_COMPLETE")


if __name__ == "__main__":
    main()
