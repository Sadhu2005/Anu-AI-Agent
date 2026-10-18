#!/usr/bin/env python3
"""V0.6.3: observation capture, memory provenance, expiry, retrieval."""

import importlib.util
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-observation-memory-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("obs_mem_t", MOD)
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
            project_id INTEGER,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            parent_task_id INTEGER,
            task_key TEXT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 5,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        "INSERT INTO goals(description, status) VALUES ('mem test', 'active')"
    )
    db.execute(
        "INSERT INTO tasks(goal_id, title, status) VALUES (1, 't1', 'pending')"
    )
    db.commit()
    return path, db


def test_capture_and_provenance():
    m = load()
    path, db = make_db()
    try:
        m.migrate(db)
        result = m.record_capability_result(
            db,
            project_key="sandbox-test-project",
            capability="git.status",
            ok=False,
            exit_code=1,
            task_id=1,
            goal_id=1,
            error="test failed",
        )
        mem = result["memory"]
        assert mem["observation_id"] == result["observation_id"]
        assert mem["project_key"] == "sandbox-test-project"
        assert mem["failure_class"] is not None
        assert mem["expires_at"] is not None
        obs = db.execute(
            "SELECT content FROM observations WHERE id = ?",
            (result["observation_id"],),
        ).fetchone()
        assert obs is not None
        print("PASS capture + provenance observation_id")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def test_retrieval_repeated_failure():
    m = load()
    path, db = make_db()
    try:
        m.migrate(db)
        for _ in range(2):
            m.record_capability_result(
                db,
                project_key="demo-project",
                capability="python.test",
                ok=False,
                exit_code=1,
                error="unittest fail",
            )
        pack = m.retrieve_for_planning(db, project_key="demo-project")
        assert pack["repeated_failure"] is True
        assert len(pack["memories"]) >= 2
        assert pack["hints"]
        print("PASS retrieval marks repeated_failure")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def test_expiry_and_prune():
    m = load()
    path, db = make_db()
    try:
        m.migrate(db)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        result = m.record_capability_result(
            db,
            project_key="demo-project",
            capability="git.status",
            ok=True,
            ttl_hours=0,
        )
        # Force expires_at into the past.
        db.execute(
            "UPDATE agent_memories SET expires_at = ? WHERE id = ?",
            (past, result["memory"]["id"]),
        )
        db.commit()
        active = m.list_active_memories(db, project_key="demo-project")
        assert active == []
        stats = m.expire_and_prune(db, project_key="demo-project", max_per_project=2)
        assert stats["expired"] >= 1

        for i in range(5):
            m.record_capability_result(
                db,
                project_key="demo-project",
                capability="git.status",
                ok=True,
                ttl_hours=24,
            )
        stats = m.expire_and_prune(db, project_key="demo-project", max_per_project=2)
        assert stats["pruned"] >= 3
        left = m.list_active_memories(db, project_key="demo-project", limit=20)
        assert len(left) <= 2
        print("PASS expiry filter and prune bound")
    finally:
        db.close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    print("===== ANU V0.6.3 OBSERVATION/MEMORY =====")
    test_capture_and_provenance()
    test_retrieval_repeated_failure()
    test_expiry_and_prune()
    print("OBSERVATION_MEMORY_TEST_COMPLETE")
