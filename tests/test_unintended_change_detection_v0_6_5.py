#!/usr/bin/env python3
"""V0.6.5: unintended file changes → ROLLBACK."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-verification-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("verification_unint", MOD)
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
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        """
    )
    db.execute("INSERT INTO goals (description) VALUES ('g')")
    db.execute("INSERT INTO tasks (goal_id, title) VALUES (1, 't')")
    db.commit()
    return db


def test_diff_scope_helper():
    m = load()
    ok, extras = m.check_diff_scope(["app.py"], ["app.py"])
    assert ok and extras == []
    ok, extras = m.check_diff_scope(["app.py"], ["app.py", "secrets.env"])
    assert not ok
    assert extras == ["secrets.env"]
    print("PASS check_diff_scope")


def test_parse_git_status_short():
    m = load()
    paths = m.parse_git_status_short(" M app.py\n?? leak.txt\n")
    assert "app.py" in paths
    assert "leak.txt" in paths
    print("PASS parse_git_status_short")


def test_unintended_forces_rollback_decision():
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
                    "baseline_sha256": "a",
                    "after_sha256": "b",
                }
            ],
            "actual_changed_files": ["app.py", "other.py"],
        },
        task_id=1,
        attempt=1,
    )
    assert record["decision"] == "ROLLBACK", record
    assert record["unintended_ok"] is False
    assert "other.py" in record["evidence"]["unintended_extras"]
    outcome = m.decision_to_task_outcome(record["decision"])
    assert outcome["task_status"] == "blocked"
    print("PASS unintended -> ROLLBACK")


def main():
    print("===== ANU V0.6.5 UNINTENDED CHANGE =====")
    test_diff_scope_helper()
    test_parse_git_status_short()
    test_unintended_forces_rollback_decision()
    print("UNINTENDED_CHANGE_TEST_COMPLETE")


if __name__ == "__main__":
    main()
