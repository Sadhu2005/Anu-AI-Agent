#!/usr/bin/env python3
"""F6: mutation agent_events rows carry goal_id from execution_context."""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
from pathlib import Path


def _ensure_fcntl_stub():
    if sys.platform == "win32" and "fcntl" not in sys.modules:
        stub = types.ModuleType("fcntl")
        stub.LOCK_SH = 1
        stub.flock = lambda *args, **kwargs: None
        sys.modules["fcntl"] = stub


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-safe-mutation-v0.2.py"


def make_brain(base: Path) -> Path:
    brain = base / "brain"
    brain.mkdir(parents=True)
    db_path = brain / "anu-brain.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            parent_task_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            phase TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL,
            task_id INTEGER,
            goal_id INTEGER,
            parent_task_id INTEGER,
            run_id TEXT,
            attempt INTEGER
        );
        CREATE TABLE mutation_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT,
            file_path TEXT,
            operation TEXT,
            status TEXT,
            baseline_ok INTEGER,
            mutation_test_ok INTEGER,
            rollback_attempted INTEGER,
            rollback_verified INTEGER,
            checkpoint_path TEXT,
            details TEXT,
            created_at TEXT,
            transaction_id TEXT,
            task_id INTEGER,
            files_changed INTEGER,
            baseline_sha256 TEXT,
            rollback_sha256 TEXT,
            integrity_verified INTEGER,
            completed_at TEXT,
            idempotency_key TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO tasks(id, goal_id, parent_task_id) VALUES (42, 7, NULL)"
    )
    db.commit()
    db.close()
    (base / "logs").mkdir(parents=True, exist_ok=True)
    (base / "checkpoints" / "mutations").mkdir(parents=True, exist_ok=True)
    (base / "runtime").mkdir(parents=True, exist_ok=True)
    return db_path


def load_sm(base: Path):
    _ensure_fcntl_stub()
    os.environ["ANU_BASE"] = str(base)
    spec = importlib.util.spec_from_file_location("sm_f6_v062", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_checkpoint_event_includes_goal_id():
    tmp = Path(tempfile.mkdtemp(prefix="anu-f6-"))
    try:
        make_brain(tmp)
        sm = load_sm(tmp)
        os.environ["ANU_TASK_ID"] = "42"
        os.environ["ANU_RUN_ID"] = "run-f6"
        os.environ["ANU_ATTEMPT"] = "1"

        sm.checkpoint_event("tx-f6", None, "baseline", "ok", {"note": "test"})

        db = sqlite3.connect(str(tmp / "brain" / "anu-brain.db"))
        row = db.execute(
            """SELECT goal_id, task_id, event_type, metadata
               FROM agent_events ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        db.close()

        assert row is not None
        assert row[0] == 7, "goal_id column"
        assert row[1] == 42, "task_id column"
        assert row[2] == "MUTATION_CHECKPOINT"
        meta = json.loads(row[3])
        assert meta.get("task_id") == 42
        print("PASS checkpoint_event goal_id column from execution_context")
    finally:
        for key in ("ANU_TASK_ID", "ANU_RUN_ID", "ANU_ATTEMPT"):
            os.environ.pop(key, None)
        os.environ.pop("ANU_BASE", None)


def test_persist_includes_goal_id():
    tmp = Path(tempfile.mkdtemp(prefix="anu-f6-persist-"))
    try:
        make_brain(tmp)
        sm = load_sm(tmp)
        os.environ["ANU_TASK_ID"] = "42"

        tx = {
            "project": "demo",
            "primary_file": "app.py",
            "operation": "python_test",
            "status": "COMMITTED",
            "baseline_ok": True,
            "mutation_test_ok": True,
            "rollback_attempted": False,
            "rollback_verified": False,
            "checkpoint_path": "/tmp/cp",
            "details": {},
            "created_at": sm.now_iso(),
            "transaction_id": "tx-persist-f6",
            "task_id": 42,
            "files": [{"path": "app.py"}],
            "baseline_sha256": "abc",
            "rollback_sha256": None,
            "integrity_verified": True,
            "completed_at": sm.now_iso(),
            "idempotency_key": "key-f6",
        }
        sm.record_started(tx)
        sm.persist(tx)

        db = sqlite3.connect(str(tmp / "brain" / "anu-brain.db"))
        row = db.execute(
            """SELECT goal_id, event_type, metadata
               FROM agent_events
               WHERE event_type = 'MUTATION_TRANSACTION'
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        db.close()

        assert row[0] == 7
        meta = json.loads(row[2])
        assert meta.get("goal_id") == 7
        print("PASS persist MUTATION_TRANSACTION goal_id")
    finally:
        os.environ.pop("ANU_TASK_ID", None)
        os.environ.pop("ANU_BASE", None)


if __name__ == "__main__":
    print("===== ANU F6 MUTATION GOAL ATTRIBUTION =====")
    test_checkpoint_event_includes_goal_id()
    test_persist_includes_goal_id()
    print("ALL PASS")
