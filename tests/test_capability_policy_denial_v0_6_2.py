#!/usr/bin/env python3
"""V0.6.2 step 2: capability policy denial + audit events."""

import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-capability-registry-v0.1.py"
GATEWAY = REPO / "config" / "anu-command-gateway.yaml"
PROFILES = REPO / "config" / "anu-execution-profiles.yaml"


def load():
    spec = importlib.util.spec_from_file_location("cap_policy_v062", MODULE)
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
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            task_id INTEGER,
            goal_id INTEGER,
            parent_task_id INTEGER,
            run_id TEXT,
            attempt INTEGER
        );
        """
    )
    db.commit()
    return path, db


def event_types(db):
    return [
        row["event_type"]
        for row in db.execute(
            "SELECT event_type FROM agent_events ORDER BY id"
        ).fetchall()
    ]


def test_admit_success_audits():
    mod = load()
    registry = mod.CapabilityRegistry.from_files(GATEWAY, PROFILES)
    path, db = make_db()
    try:
        decision = registry.evaluate_request(
            "git.status",
            {"project": "sandbox-test-project", "task_id": 9},
            db=db,
            task_id=9,
        )
        assert decision["admitted"] is True
        assert decision["command"] == "git_status"
        assert event_types(db) == [
            "CAPABILITY_REQUEST",
            "CAPABILITY_ADMITTED",
        ]
        meta = json.loads(
            db.execute(
                "SELECT metadata FROM agent_events "
                "WHERE event_type='CAPABILITY_ADMITTED'"
            ).fetchone()["metadata"]
        )
        assert meta["profile"] == "git-read"
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS admit success audits REQUEST+ADMITTED")


def test_deny_unknown_and_disabled_audits():
    mod = load()
    registry = mod.CapabilityRegistry.from_files(GATEWAY, PROFILES)
    path, db = make_db()
    try:
        unknown = registry.evaluate_request(
            "filesystem.write",
            {"project": "sandbox-test-project"},
            db=db,
        )
        assert unknown["admitted"] is False
        assert unknown["denial_class"] == "unknown"

        disabled = registry.evaluate_request(
            "research",
            {"project": "sandbox-test-project"},
            db=db,
        )
        assert disabled["admitted"] is False
        assert disabled["denial_class"] == "disabled"

        assert event_types(db) == [
            "CAPABILITY_REQUEST",
            "CAPABILITY_DENIED",
            "CAPABILITY_REQUEST",
            "CAPABILITY_DENIED",
        ]
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS deny unknown/disabled audits REQUEST+DENIED")


def test_deny_contract_missing_project():
    mod = load()
    registry = mod.CapabilityRegistry.from_files(GATEWAY, PROFILES)
    path, db = make_db()
    try:
        decision = registry.evaluate_request(
            "python.test",
            {},
            db=db,
            goal_id=3,
        )
        assert decision["admitted"] is False
        assert decision["denial_class"] == "contract"
        assert event_types(db) == [
            "CAPABILITY_REQUEST",
            "CAPABILITY_DENIED",
        ]
        row = db.execute(
            "SELECT goal_id, metadata FROM agent_events "
            "WHERE event_type='CAPABILITY_DENIED'"
        ).fetchone()
        assert row["goal_id"] == 3
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS deny contract missing project")


if __name__ == "__main__":
    print("===== ANU V0.6.2 CAPABILITY POLICY DENIAL REGRESSION =====")
    test_admit_success_audits()
    test_deny_unknown_and_disabled_audits()
    test_deny_contract_missing_project()
    print("CAPABILITY_POLICY_DENIAL_TEST_COMPLETE")
