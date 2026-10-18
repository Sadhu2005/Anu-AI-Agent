#!/usr/bin/env python3
"""V0.7.0: resource defer parks work; wake re-eval resumes it."""

import importlib.util
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
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
        "INSERT INTO tasks (goal_id, title, status) VALUES (1, 't', 'waiting')"
    )
    db.commit()
    return path, db


def test_resource_wake_resumes_pending():
    startup = load(
        REPO / "bin" / "anu-startup-recovery-v0.1.py",
        "startup_resource_v070",
    )
    path, db = make_db()
    try:
        startup.migrate(db)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        startup.register_wake(
            db,
            wake_kind=startup.WAKE_RESOURCE,
            task_id=1,
            goal_id=1,
            wake_at=past,
            payload={"reason": "hot_pi"},
        )
        wakes = startup.evaluate_wake_conditions(db)
        assert len(wakes["satisfied"]) >= 1
        status = db.execute(
            "SELECT status FROM tasks WHERE id = 1"
        ).fetchone()[0]
        assert status == "pending", status
    finally:
        db.close()
        path.unlink(missing_ok=True)
    print("PASS resource wake re-eval resumes waiting task")


def test_runtime_defers_then_resumes_on_normal():
    runtime_path = REPO / "bin" / "anu-runtime.py"
    root = Path(tempfile.mkdtemp())
    (root / "logs").mkdir()
    (root / "runtime").mkdir()
    (root / "logs" / "anu.log").write_text("", encoding="utf-8")
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(root)
    try:
        rt = load(runtime_path, "runtime_hot_gate_v070")
        rt.BASE = root
        rt.LOG_FILE = root / "logs" / "anu.log"
        rt.STATE_FILE = root / "runtime" / "anu-state.json"
        rt.boundary_ok = True

        skipped = rt.run_agent_scheduler_cycle(
            {"status": "CRITICAL", "temperature_c": 85}
        )
        assert skipped["status"] == "SKIPPED_RESOURCE"

        called = {"n": 0}

        class FakeSched:
            def run_agent_scheduler_cycle_once(self, **kwargs):
                called["n"] += 1
                return {"status": "OK"}

        rt._load_scheduler = lambda: FakeSched()
        ok = rt.run_agent_scheduler_cycle({"status": "NORMAL"})
        assert ok["status"] == "OK"
        assert called["n"] == 1
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS resource pressure defers schedule; NORMAL resumes")


if __name__ == "__main__":
    print("===== ANU V0.7.0 GOAL RESOURCE DEFER/RESUME =====")
    test_resource_wake_resumes_pending()
    test_runtime_defers_then_resumes_on_normal()
    print("GOAL_RESOURCE_DEFER_RESUME_TEST_COMPLETE")
