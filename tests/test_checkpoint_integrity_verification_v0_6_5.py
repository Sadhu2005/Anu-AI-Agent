#!/usr/bin/env python3
"""V0.6.5: checkpoint integrity evidence in verification records."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-verification-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("verification_cp", MOD)
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
            description TEXT NOT NULL
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            title TEXT NOT NULL
        );
        """
    )
    db.execute("INSERT INTO goals (description) VALUES ('g')")
    db.execute("INSERT INTO tasks (goal_id, title) VALUES (1, 't')")
    db.commit()
    return db


def test_checkpoint_ok_with_digests():
    m = load()
    assert m.check_checkpoint_integrity(
        [{"path": "app.py", "baseline_sha256": "abc"}]
    )
    assert not m.check_checkpoint_integrity([{"path": "app.py"}])
    assert not m.check_checkpoint_integrity([])
    print("PASS check_checkpoint_integrity")


def test_rollback_verification_failed_is_rollback():
    m = load()
    db = make_db()
    m.migrate(db)
    record = m.evaluate_mutation_attempt(
        db,
        {
            "status": "ROLLBACK_VERIFICATION_FAILED",
            "files": ["app.py"],
            "mutation_test_ok": False,
            "rollback_attempted": True,
            "rollback_verified": False,
            "integrity_verified": False,
        },
        task_id=1,
        attempt=2,
    )
    assert record["decision"] == "ROLLBACK", record
    assert record["checkpoint_ok"] is False or record["rollback_ok"] is False
    outcome = m.decision_to_task_outcome(
        record["decision"],
        mutation_status="ROLLBACK_VERIFICATION_FAILED",
    )
    assert outcome["task_status"] == "blocked"
    assert outcome["failure_class"] == "integrity"
    print("PASS ROLLBACK_VERIFICATION_FAILED -> ROLLBACK")


def test_intended_unchanged_blocks_accept():
    m = load()
    db = make_db()
    m.migrate(db)
    record = m.evaluate_mutation_attempt(
        db,
        {
            "status": "COMMITTED",
            "operation": "python_test",
            "files": ["app.py"],
            "mutation_test_ok": True,
            "integrity_verified": True,
            "file_records": [
                {
                    "path": "app.py",
                    "baseline_sha256": "same",
                    "after_sha256": "same",
                }
            ],
            "actual_changed_files": [],
        },
        task_id=1,
        attempt=1,
    )
    assert record["decision"] == "ROLLBACK", record
    assert record["intended_ok"] is False
    print("PASS no intended change -> ROLLBACK")


def main():
    print("===== ANU V0.6.5 CHECKPOINT INTEGRITY =====")
    test_checkpoint_ok_with_digests()
    test_rollback_verification_failed_is_rollback()
    test_intended_unchanged_blocks_accept()
    print("CHECKPOINT_INTEGRITY_TEST_COMPLETE")


if __name__ == "__main__":
    main()
