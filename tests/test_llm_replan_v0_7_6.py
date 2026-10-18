#!/usr/bin/env python3
"""V0.7.6: recovery replan uses LLM planner when planner.mode=llm_optional."""

import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_recovery():
    mod = load(REPO / "bin" / "anu-goal-recovery-v0.1.py", "goal_recovery_v076")
    mod.BASE = REPO
    return mod


def load_template_planner():
    mod = load(
        REPO / "bin" / "anu-template-planner-v0.1.py",
        "template_planner_v076",
    )
    mod.BASE = REPO
    return mod


def make_expanded_db():
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
            failure_class TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(goal_id) REFERENCES goals(id)
        );
        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, depends_on_task_id),
            CHECK(task_id != depends_on_task_id),
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(depends_on_task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
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
        """
    )
    db.execute(
        "INSERT INTO goals(description, status) VALUES ('Improve', 'pending')"
    )
    db.commit()
    planner = load_template_planner()
    expansion = planner.expand_goal(
        db,
        1,
        project="sandbox-test-project",
        plan_id="plan-rec-1",
    )
    return path, db, expansion


def _patch_planner(recovery, mode, gateway_call=None):
    recovery._planner_mode = lambda: mode
    orig = recovery._load_planner

    def patched():
        planner = orig()
        if gateway_call is not None and hasattr(planner, "_gateway_call"):
            planner._gateway_call = gateway_call
        return planner

    recovery._load_planner = patched


def _valid_steps_json(n=2):
    steps = []
    for i in range(n):
        op = "python_compile" if i % 2 else "python_test"
        steps.append({
            "role": f"step{i}",
            "description": f"do step {i}",
            "operation": op,
        })
    return json.dumps({"steps": steps})


def test_replan_llm_optional_source_llm():
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    recovery = load_recovery()
    calls = []

    def fake(op, **fields):
        calls.append(op)
        if op == "health":
            return {"ok": True}
        return {"ok": True, "text": _valid_steps_json(2)}

    _patch_planner(recovery, "llm_optional", fake)
    path, db, expansion = make_expanded_db()
    try:
        result = recovery.replan_goal(
            db,
            1,
            "retry with llm plan",
            plan_id="plan-rec-llm",
            project="sandbox-test-project",
        )
        assert result["policy"] == "replan"
        assert result["source"] == "llm"
        assert result["expansion"]["source"] == "llm"
        assert len(result["expansion"]["tasks"]) == 2
        assert "health" in calls and "chat" in calls

        meta = db.execute(
            "SELECT metadata FROM agent_events "
            "WHERE event_type='GOAL_REPLANNED' ORDER BY id DESC LIMIT 1"
        ).fetchone()["metadata"]
        payload = json.loads(meta)
        assert payload["source"] == "llm"
    finally:
        db.close()
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS replan llm_optional -> source=llm")


def test_replan_gateway_down_template_fallback():
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    recovery = load_recovery()

    def fake(op, **fields):
        raise ConnectionError("socket missing")

    _patch_planner(recovery, "llm_optional", fake)
    path, db, expansion = make_expanded_db()
    try:
        result = recovery.replan_goal(
            db,
            1,
            "gateway down",
            plan_id="plan-rec-fb",
            project="sandbox-test-project",
        )
        assert result["source"] == "template_fallback"
        assert len(result["expansion"]["tasks"]) == 6
        meta = json.loads(
            db.execute(
                "SELECT metadata FROM agent_events "
                "WHERE event_type='GOAL_REPLANNED' ORDER BY id DESC LIMIT 1"
            ).fetchone()["metadata"]
        )
        assert meta["source"] == "template_fallback"
    finally:
        db.close()
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS replan gateway down -> template_fallback")


def test_replan_template_only_never_calls_gateway():
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    recovery = load_recovery()
    called = []

    def fake(op, **fields):
        called.append(op)
        raise AssertionError("gateway must not be called")

    _patch_planner(recovery, "template_only", fake)
    path, db, expansion = make_expanded_db()
    try:
        result = recovery.replan_goal(
            db,
            1,
            "template only",
            plan_id="plan-rec-tpl",
            project="sandbox-test-project",
        )
        # Template planner does not set source; recovery defaults to "template"
        assert result["source"] in ("template", "template_only")
        assert len(result["expansion"]["tasks"]) == 6
        assert called == []
    finally:
        db.close()
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS template_only never calls gateway")


if __name__ == "__main__":
    print("===== ANU V0.7.6 LLM REPLAN =====")
    test_replan_llm_optional_source_llm()
    test_replan_gateway_down_template_fallback()
    test_replan_template_only_never_calls_gateway()
    print("LLM_REPLAN_TEST_COMPLETE")
