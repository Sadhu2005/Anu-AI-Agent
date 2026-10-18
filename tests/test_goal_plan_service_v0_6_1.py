#!/usr/bin/env python3
"""V0.6.1 step 3: versioned goal plan persistence."""

import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-goal-plan-service-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("goal_plan_svc_v061", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.MODEL_MODULE = REPO / "bin" / "anu-agent-model-v0.1.py"
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
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            goal_id INTEGER,
            parent_task_id INTEGER,
            run_id TEXT,
            attempt INTEGER,
            event_type TEXT NOT NULL,
            message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        "INSERT INTO goals(description, status) VALUES ('Improve sandbox', 'pending')"
    )
    db.commit()
    return path, db


def sample_plan(goal_id=1, plan_id="plan-alpha", action_id="act-1"):
    return {
        "plan_id": plan_id,
        "goal_id": str(goal_id),
        "actions": [
            {
                "action_id": action_id,
                "goal_id": str(goal_id),
                "kind": "TASK",
                "description": "inspect project",
                "parameters": {"step": "inspect"},
            }
        ],
    }


def test_migrate_save_version_and_supersede():
    ps = load()
    path, db = make_db()
    try:
        ps.migrate(db)
        ps.migrate(db)  # idempotent

        v1 = ps.save_plan(db, 1, sample_plan(plan_id="plan-v1", action_id="a1"))
        assert v1["version"] == 1
        assert v1["status"] == "active"
        assert v1["body"]["actions"][0]["description"] == "inspect project"

        active = ps.get_active_plan(db, 1)
        assert active["plan_id"] == "plan-v1"

        v2 = ps.save_plan(db, 1, sample_plan(plan_id="plan-v2", action_id="a2"))
        assert v2["version"] == 2
        assert v2["status"] == "active"

        old = ps.get_plan(db, "plan-v1")
        assert old["status"] == "superseded"
        assert ps.get_active_plan(db, 1)["plan_id"] == "plan-v2"

        versions = ps.list_plans(db, 1)
        assert [p["version"] for p in versions] == [2, 1]
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS migrate/save/version/supersede")


def test_validation_and_abandon():
    ps = load()
    path, db = make_db()
    try:
        ps.migrate(db)

        try:
            ps.save_plan(db, 999, sample_plan())
            raise AssertionError("missing goal should fail")
        except ValueError as exc:
            assert "does not exist" in str(exc)

        try:
            ps.save_plan(
                db,
                1,
                {
                    "plan_id": "plan-bad",
                    "goal_id": "2",
                    "actions": [],
                },
            )
            raise AssertionError("mismatched goal_id should fail")
        except ValueError as exc:
            assert "does not match" in str(exc)

        saved = ps.save_plan(db, 1, sample_plan(plan_id="plan-keep"))
        abandoned = ps.abandon_active_plan(db, 1, reason="operator cancel")
        assert abandoned["status"] == "abandoned"
        assert ps.get_active_plan(db, 1) is None
        assert ps.get_plan(db, saved["plan_id"])["status"] == "abandoned"
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS validation and abandon")


def test_cli_migrate_and_save():
    ps = load()
    path, db = make_db()
    db.close()
    try:
        rc = ps.main(["--db", str(path), "migrate"])
        assert rc == 0
        plan_json = json.dumps(sample_plan(plan_id="plan-cli"))
        rc = ps.main([
            "--db", str(path),
            "save",
            "--goal-id", "1",
            "--plan-json", plan_json,
        ])
        assert rc == 0
        db2 = ps.db_connect(path)
        active = ps.get_active_plan(db2, 1)
        assert active["plan_id"] == "plan-cli"
        db2.close()
    finally:
        path.unlink(missing_ok=True)

    print("PASS CLI migrate and save")


if __name__ == "__main__":
    print("===== ANU V0.6.1 GOAL PLAN SERVICE REGRESSION =====")
    test_migrate_save_version_and_supersede()
    test_validation_and_abandon()
    test_cli_migrate_and_save()
    print("GOAL_PLAN_SERVICE_TEST_COMPLETE")
