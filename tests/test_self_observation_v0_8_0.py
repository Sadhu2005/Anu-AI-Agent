#!/usr/bin/env python3
"""V0.8.0: verification decisions → observation/memory + planning hints."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
VERIFY_MOD = REPO / "bin" / "anu-verification-v0.1.py"
MEM_MOD = REPO / "bin" / "anu-observation-memory-v0.1.py"


def load_verify():
    spec = importlib.util.spec_from_file_location("verify_v080", VERIFY_MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_mem():
    spec = importlib.util.spec_from_file_location("mem_v080", MEM_MOD)
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
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            observation_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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


def _committed_payload(**extra):
    base = {
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
    }
    base.update(extra)
    return base


def test_accept_records_memory():
    v = load_verify()
    mem = load_mem()
    _root, _db_path, db = make_db()
    try:
        v.migrate(db)
        record = v.evaluate_mutation_attempt(
            db,
            _committed_payload(),
            task_id=1,
            attempt=1,
            goal_id=1,
            run_id="run-accept",
        )
        assert record["decision"] == "ACCEPT", record
        assert record.get("memory_id"), record
        row = db.execute(
            "SELECT observation_type, content FROM observations "
            "WHERE id = ?",
            (record["observation_id"],),
        ).fetchone()
        assert row["observation_type"] == "verification_outcome"
        memory = mem.get_memory(db, record["memory_id"])
        assert memory["project_key"] == "sandbox"
        assert "verification" in memory["tags"]
        assert "decision:ACCEPT" in memory["tags"]
        assert memory["failure_class"] is None
        assert memory["summary"].startswith("verification ACCEPT:")
        print("PASS ACCEPT -> observation + memory tags")
    finally:
        db.close()


def test_retry_records_memory():
    v = load_verify()
    mem = load_mem()
    _root, _db_path, db = make_db()
    try:
        v.migrate(db)
        record = v.evaluate_mutation_attempt(
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
            goal_id=1,
        )
        assert record["decision"] == "RETRY", record
        memory = mem.get_memory(db, record["memory_id"])
        assert "decision:RETRY" in memory["tags"]
        assert memory["failure_class"] is not None
        assert "verification RETRY:" in memory["summary"]
        print("PASS RETRY -> observation + memory tags")
    finally:
        db.close()


def test_rollback_records_memory():
    v = load_verify()
    mem = load_mem()
    _root, _db_path, db = make_db()
    try:
        v.migrate(db)
        record = v.evaluate_mutation_attempt(
            db,
            _committed_payload(
                actual_changed_files=["app.py", "extra.py"],
            ),
            task_id=1,
            attempt=1,
            goal_id=1,
        )
        assert record["decision"] == "ROLLBACK", record
        memory = mem.get_memory(db, record["memory_id"])
        assert "decision:ROLLBACK" in memory["tags"]
        assert memory["failure_class"] is not None
        assert "scope mismatch" in memory["summary"] or "ROLLBACK" in memory[
            "summary"
        ]
        print("PASS ROLLBACK -> observation + memory tags")
    finally:
        db.close()


def test_retrieve_for_planning_includes_verification_hint():
    v = load_verify()
    mem = load_mem()
    _root, _db_path, db = make_db()
    try:
        v.migrate(db)
        v.evaluate_mutation_attempt(
            db,
            _committed_payload(),
            task_id=1,
            attempt=1,
            goal_id=1,
        )
        pack = mem.retrieve_for_planning(db, project_key="sandbox")
        assert pack["hints"], pack
        assert any("verification" in h for h in pack["hints"]), pack["hints"]
        assert any(
            "decision:ACCEPT" in (m.get("tags") or [])
            for m in pack["memories"]
        ), pack["memories"]
        print("PASS retrieve_for_planning includes verification hint")
    finally:
        db.close()


def test_memory_failure_does_not_break_verify():
    v = load_verify()
    _root, _db_path, db = make_db()
    try:
        v.migrate(db)

        class Boom:
            def migrate(self, _db):
                raise RuntimeError("boom migrate")

            def record_verification_outcome(self, *_a, **_k):
                raise RuntimeError("boom record")

        original = v._load_observation_memory
        v._load_observation_memory = lambda: Boom()
        try:
            record = v.evaluate_mutation_attempt(
                db,
                _committed_payload(),
                task_id=1,
                attempt=1,
                goal_id=1,
            )
        finally:
            v._load_observation_memory = original

        assert record["decision"] == "ACCEPT", record
        assert record.get("id") is not None
        assert record.get("memory_id") is None
        print("PASS memory failure does not break verify")
    finally:
        db.close()


def test_record_verification_outcome_direct():
    mem = load_mem()
    _root, _db_path, db = make_db()
    try:
        mem.migrate(db)
        result = mem.record_verification_outcome(
            db,
            project_key="sandbox",
            decision="ACCEPT",
            operation="python_test",
            task_id=1,
            goal_id=1,
        )
        memory = result["memory"]
        assert memory["summary"] == "verification ACCEPT: python_test"
        assert "decision:ACCEPT" in memory["tags"]
        assert memory["confidence"] == 0.85
        print("PASS record_verification_outcome direct API")
    finally:
        db.close()


def main():
    test_record_verification_outcome_direct()
    test_accept_records_memory()
    test_retry_records_memory()
    test_rollback_records_memory()
    test_retrieve_for_planning_includes_verification_hint()
    test_memory_failure_does_not_break_verify()
    print("ALL V0.8.0 self-observation tests PASS")


if __name__ == "__main__":
    main()
