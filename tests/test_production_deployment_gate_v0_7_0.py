#!/usr/bin/env python3
"""V0.7.0: production_deployment gate requires approval then proceeds."""

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
        "INSERT INTO tasks (goal_id, title, status) VALUES (1, 'deploy', 'pending')"
    )
    db.commit()
    return path, db


def test_gate_requests_approval_and_parks():
    pi = load(
        REPO / "bin" / "anu-project-intelligence-v0.1.py",
        "pi_gate_v070",
    )
    approval = load(
        REPO / "bin" / "anu-approval-service-v0.1.py",
        "approval_gate_v070",
    )
    path, db = make_db()
    try:
        result = pi.gate_production_deployment(
            db,
            task_id=1,
            goal_id=1,
            proposed_change="deploy sandbox",
        )
        assert result["classification"] == "APPROVAL"
        assert result["approval"]["status"] == "pending"
        status = db.execute(
            "SELECT status FROM tasks WHERE id = 1"
        ).fetchone()[0]
        assert status == "waiting", status

        approved = approval.approve(
            db,
            result["approval"]["id"],
            decided_by="tester",
            note="ship it",
        )
        assert approved["status"] == "approved"

        again = pi.gate_production_deployment(
            db,
            task_id=1,
            goal_id=1,
            proposed_change="deploy sandbox",
        )
        assert again["classification"] == "AUTONOMOUS"
        assert again.get("reason") == "prior_approval"
    finally:
        db.close()
        path.unlink(missing_ok=True)
    print("PASS production gate: wait then prior_approval proceeds")


def test_safe_mutation_wires_gate_helper():
    # Source-level check: importing safe-mutation needs fcntl (Linux/Pi).
    source = (REPO / "bin" / "anu-safe-mutation-v0.2.py").read_text(
        encoding="utf-8"
    )
    assert "--production" in source
    assert "gate_production_deployment" in source
    assert "def _gate_production_deployment" in source
    print("PASS safe-mutation wires --production gate")


if __name__ == "__main__":
    print("===== ANU V0.7.0 PRODUCTION DEPLOYMENT GATE =====")
    test_gate_requests_approval_and_parks()
    test_safe_mutation_wires_gate_helper()
    print("PRODUCTION_DEPLOYMENT_GATE_TEST_COMPLETE")
