#!/usr/bin/env python3
"""V0.8.4: activity reports surface TASK_RECOVERY_POLICY clearly."""

import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-activity-report-v0.1.py"
MEM_MOD = REPO / "bin" / "anu-observation-memory-v0.1.py"


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
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT 'Sandbox',
            repository TEXT NOT NULL DEFAULT 'local',
            workspace TEXT NOT NULL DEFAULT '/tmp',
            status TEXT NOT NULL DEFAULT 'active'
        );
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
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL
        );
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
        "INSERT INTO projects(project_key, name, repository, workspace) "
        "VALUES ('sandbox-test-project', 'Sandbox', 'local', '/tmp/s')"
    )
    db.execute(
        "INSERT INTO goals(project_id, description, status) "
        "VALUES (1, 'recovery report smoke', 'active')"
    )
    db.execute(
        "INSERT INTO tasks(goal_id, title, status, attempts) "
        "VALUES (1, 't1', 'failed', 1)"
    )
    db.execute(
        "INSERT INTO task_execution_specs(task_id, project, changes_file, operation) "
        "VALUES (1, 'sandbox-test-project', 'changes.json', 'python_test')"
    )
    db.commit()
    return path, db


def test_goal_report_enriches_recovery_policy():
    rep = load(MOD, "rep_v084")
    rep.BASE = REPO
    path, db = make_db()
    try:
        meta = {
            "action": "retry",
            "reason": "verification RETRY: safe to retry; backoff 2m",
            "failure_class": "code_defect",
            "retry_after": "2026-10-18T12:00:00+00:00",
            "retry_after_minutes": 2,
            "decision": "RETRY",
            "verification_id": 9,
        }
        db.execute(
            """INSERT INTO agent_events
               (event_type, message, metadata, task_id, goal_id, created_at)
               VALUES (?, ?, ?, 1, 1, ?)""",
            (
                "TASK_RECOVERY_POLICY",
                meta["reason"],
                json.dumps(meta),
                "2026-10-18T11:58:00+00:00",
            ),
        )
        db.execute(
            """INSERT INTO agent_events
               (event_type, message, metadata, task_id, goal_id, created_at)
               VALUES (?, ?, ?, 1, 1, ?)""",
            (
                "TASK_FAILED",
                "task failed",
                json.dumps({"attempt": 1}),
                "2026-10-18T11:57:00+00:00",
            ),
        )
        db.commit()

        result = rep.report_goal(db, 1)
        assert result["ok"] is True, result
        recovery_events = [
            e for e in result["events"]
            if e.get("event_type") == "TASK_RECOVERY_POLICY"
        ]
        assert len(recovery_events) == 1
        assert recovery_events[0]["recovery"]["action"] == "retry"
        assert recovery_events[0]["recovery"]["decision"] == "RETRY"
        # Unrelated events stay compact (no recovery key).
        other = [e for e in result["events"] if e["event_type"] == "TASK_FAILED"]
        assert other and "recovery" not in other[0]

        block = result["recovery"]
        assert block["policy_event_count"] == 1
        assert block["actions"] == ["retry"]
        assert block["latest"]["action"] == "retry"
        assert block["latest"]["task_id"] == 1
        print("PASS goal report enriches TASK_RECOVERY_POLICY")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def test_missing_metadata_safe():
    rep = load(MOD, "rep_v084b")
    rep.BASE = REPO
    path, db = make_db()
    try:
        db.execute(
            """INSERT INTO agent_events
               (event_type, message, metadata, task_id, goal_id)
               VALUES ('TASK_RECOVERY_POLICY', 'bare', NULL, 1, 1)"""
        )
        db.execute(
            """INSERT INTO agent_events
               (event_type, message, metadata, task_id, goal_id)
               VALUES ('TASK_RECOVERY_POLICY', 'badjson', '{not-json', 1, 1)"""
        )
        db.commit()
        result = rep.report_goal(db, 1)
        assert result["ok"] is True
        assert result["recovery"]["policy_event_count"] == 0
        assert result["recovery"]["latest"] is None
        print("PASS missing/bad metadata is safe")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def test_memory_recovery_tags_and_project_summary():
    rep = load(MOD, "rep_v084c")
    mem = load(MEM_MOD, "mem_v084")
    rep.BASE = REPO
    path, db = make_db()
    try:
        mem.migrate(db)
        mem.record_verification_outcome(
            db,
            project_key="sandbox-test-project",
            decision="RETRY",
            operation="python_test",
            recovery_action="wait",
            evidence={"questions": {"compile": False}},
        )
        meta = {
            "action": "escalate",
            "reason": "verification RETRY exhausted",
            "decision": "RETRY",
        }
        db.execute(
            """INSERT INTO agent_events
               (event_type, message, metadata, task_id, goal_id)
               VALUES (?, ?, ?, 1, 1)""",
            ("TASK_RECOVERY_POLICY", meta["reason"], json.dumps(meta)),
        )
        db.commit()

        goal = rep.report_goal(db, 1)
        assert "recovery:wait" in goal["recovery"]["memory_recovery_tags"]

        # Minimal project intelligence stub: avoid full dossier dependency.
        def fake_dossier(db_arg, key):
            return {
                "project_key": key,
                "registered": True,
                "open_goals": 1,
                "open_tasks": 1,
                "verification_hint_count": 0,
                "memory_repeated_failure": False,
                "recent_goal_ids": [1],
            }

        rep._dossier_best_effort = fake_dossier
        project = rep.report_project(db, "sandbox-test-project")
        assert project["ok"] is True, project
        assert project["summary"]["recovery_policy_event_count"] >= 1
        assert project["summary"]["latest_recovery_action"] == "escalate"
        print("PASS memory recovery tags + project summary")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def main():
    test_goal_report_enriches_recovery_policy()
    test_missing_metadata_safe()
    test_memory_recovery_tags_and_project_summary()
    print("ALL V0.8.4 recovery report tests PASS")


if __name__ == "__main__":
    main()
