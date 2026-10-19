#!/usr/bin/env python3
"""V0.8.5: research capability is approval-gated at evaluate/invoke."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
CAP = REPO / "bin" / "anu-capability-registry-v0.1.py"
APPROVAL = REPO / "bin" / "anu-approval-service-v0.1.py"
GATEWAY = REPO / "config" / "anu-command-gateway.yaml"
PROFILES = REPO / "config" / "anu-execution-profiles.yaml"


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
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
            parent_task_id INTEGER,
            run_id TEXT,
            attempt INTEGER,
            event_type TEXT,
            message TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE research (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            url TEXT,
            title TEXT,
            summary TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute("INSERT INTO goals (description) VALUES ('research goal')")
    db.execute(
        "INSERT INTO tasks (goal_id, title, status) VALUES (1, 'r', 'pending')"
    )
    db.commit()
    return db, db_path


def test_research_denied_without_approval():
    reg_mod = load_mod(CAP, "cap_gate_085")
    registry = reg_mod.CapabilityRegistry.from_files(GATEWAY, PROFILES)
    db, _ = make_db()
    decision = registry.evaluate_request(
        "research",
        {
            "project": "sandbox-test-project",
            "task_id": 1,
            "action": "fetch",
            "url": "https://docs.python.org/3/",
        },
        db=db,
        task_id=1,
    )
    assert decision["admitted"] is False
    assert decision["denial_class"] == "approval"
    print("PASS research denied without approval")


def test_research_admitted_and_invoked_with_approval():
    reg_mod = load_mod(CAP, "cap_gate_085b")
    appr = load_mod(APPROVAL, "appr_085")
    registry = reg_mod.CapabilityRegistry.from_files(GATEWAY, PROFILES)
    db, _ = make_db()
    appr.migrate(db)
    rec = appr.request_approval(
        db, action="research", task_id=1, goal_id=1, put_task_waiting=False
    )
    appr.approve(db, rec["id"], decided_by="tester", note="v0.8.5")

    admitted = registry.evaluate_request(
        "research",
        {
            "project": "sandbox-test-project",
            "task_id": 1,
            "action": "search",
            "query": "ANU agent",
        },
        db=db,
        task_id=1,
    )
    assert admitted["admitted"] is True, admitted

    fake_out = {
        "ok": True,
        "action": "search",
        "url": "https://api.duckduckgo.com/",
        "title": "ANU",
        "summary": "mock",
        "research_id": 1,
    }

    def fake_run(argv, capture_output=True, text=True, check=False):
        class P:
            returncode = 0
            stdout = json.dumps(fake_out)
            stderr = ""

        assert "anu-research-v0.1.py" in " ".join(argv)
        assert "search" in argv
        assert "--query" in argv
        return P()

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = registry.invoke(
            "research",
            {
                "project": "sandbox-test-project",
                "task_id": 1,
                "action": "search",
                "query": "ANU agent",
            },
            db=db,
            task_id=1,
        )
    assert result["ok"] is True, result
    assert result["admitted"] is True
    assert result["research_result"]["title"] == "ANU"
    print("PASS research admitted and invoked with approval")


def test_browser_still_disabled():
    reg_mod = load_mod(CAP, "cap_gate_085c")
    registry = reg_mod.CapabilityRegistry.from_files(GATEWAY, PROFILES)
    try:
        registry.require("browser")
        raise AssertionError("browser must stay disabled")
    except PermissionError as exc:
        assert "disabled" in str(exc)
    print("PASS browser still disabled")


def main():
    test_research_denied_without_approval()
    test_research_admitted_and_invoked_with_approval()
    test_browser_still_disabled()
    print("ALL_PASS test_research_capability_gate_v0_8_5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
