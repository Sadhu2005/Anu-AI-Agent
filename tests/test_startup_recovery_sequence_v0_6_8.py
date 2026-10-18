#!/usr/bin/env python3
"""V0.6.8: startup recovery sequence inventories and heartbeats."""

import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-startup-recovery-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("startup_seq", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_env():
    root = Path(tempfile.mkdtemp())
    brain = root / "brain"
    runtime = root / "runtime"
    logs = root / "logs"
    brain.mkdir()
    runtime.mkdir()
    logs.mkdir()
    db_path = brain / "anu-brain.db"
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
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
            retry_after TEXT
        );
        CREATE TABLE approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at TEXT,
            expires_at TEXT
        );
        """
    )
    db.execute("INSERT INTO goals (description, status) VALUES ('g', 'active')")
    db.execute(
        "INSERT INTO tasks (goal_id, title, status) VALUES (1, 'w', 'waiting')"
    )
    db.execute(
        "INSERT INTO tasks (goal_id, title, status) VALUES (1, 'p', 'pending')"
    )
    db.commit()
    return root, db_path, db, runtime, logs


def test_startup_sequence_skip_mutation():
    m = load()
    root, db_path, db, runtime, logs = make_env()
    m.migrate(db)
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(root)
        summary = m.run_startup_sequence(
            db_path=str(db_path),
            state_path=str(runtime / "anu-state.json"),
            log_dir=str(logs),
            skip_mutation_recovery=True,
        )
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old

    assert summary["ok"] is True
    assert summary["inventory"]["waiting_tasks"]
    assert summary["inventory"]["active_goals"]
    assert summary["heartbeat"]["status"] == "running"
    state = json.loads((runtime / "anu-state.json").read_text(encoding="utf-8"))
    assert "heartbeat_at" in state
    print("PASS startup sequence inventory + heartbeat")


def test_park_for_shutdown():
    m = load()
    root = Path(tempfile.mkdtemp())
    state = root / "anu-state.json"
    data = m.park_for_shutdown(state, reason="SIGTERM")
    assert data["status"] == "parked"
    assert data["park_reason"] == "SIGTERM"
    loaded = json.loads(state.read_text(encoding="utf-8"))
    assert loaded["status"] == "parked"
    print("PASS park for graceful shutdown")


def main():
    print("===== ANU V0.6.8 STARTUP RECOVERY SEQUENCE =====")
    test_startup_sequence_skip_mutation()
    test_park_for_shutdown()
    print("STARTUP_RECOVERY_SEQUENCE_TEST_COMPLETE")


if __name__ == "__main__":
    main()
