#!/usr/bin/env python3
"""V0.7.2: LLM planner propose/validate + template fallback (mocked gateway)."""

import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-llm-planner-v0.1.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_planner():
    planner = load(MODULE, "llm_planner_v072")
    planner.BASE = REPO
    planner._gateway_call = None
    return planner


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
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(goal_id) REFERENCES goals(id),
            FOREIGN KEY(parent_task_id) REFERENCES tasks(id)
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
        "INSERT INTO goals(description, status) VALUES (?, 'pending')",
        ("Improve sandbox project via LLM plan",),
    )
    db.commit()
    return path, db


def _valid_steps_json(n=3):
    steps = []
    for i in range(n):
        op = "python_compile" if i % 2 else "python_test"
        steps.append({
            "role": f"step{i}",
            "description": f"do step {i}",
            "operation": op,
        })
    return json.dumps({"steps": steps})


def test_validate_steps_accepts_allowed():
    p = load_planner()
    ok, steps, reason = p.validate_steps(
        json.loads(_valid_steps_json(2)),
        max_steps=8,
    )
    assert ok and reason == "ok"
    assert len(steps) == 2
    assert steps[0]["operation"] == "python_test"
    print("PASS validate_steps accepts allowed ops")


def test_validate_steps_rejects_disallowed_op():
    p = load_planner()
    ok, steps, reason = p.validate_steps(
        {
            "steps": [
                {
                    "role": "hack",
                    "description": "run shell",
                    "operation": "shell_exec",
                }
            ]
        },
        max_steps=8,
    )
    assert not ok and steps is None
    assert "bad_operation" in reason
    print("PASS validate_steps rejects disallowed operation")


def test_propose_valid_json_returns_llm():
    p = load_planner()
    calls = []

    def fake(op, **fields):
        calls.append(op)
        if op == "health":
            return {"ok": True}
        if op == "chat":
            return {"ok": True, "text": _valid_steps_json(2)}
        raise AssertionError(f"unexpected op {op}")

    p._gateway_call = fake
    out = p.propose_steps(
        {"description": "goal"},
        {"project_key": "sandbox-test-project", "file_count": 3},
        {"hints": [], "repeated_failure": False},
        project_key="sandbox-test-project",
    )
    assert out["ok"] is True
    assert out["source"] == "llm"
    assert len(out["steps"]) == 2
    assert calls == ["health", "chat"]
    print("PASS propose_steps valid JSON -> llm")


def test_propose_bad_json_falls_through():
    p = load_planner()

    def fake(op, **fields):
        if op == "health":
            return {"ok": True}
        return {"ok": True, "text": "not json at all {"}

    p._gateway_call = fake
    out = p.propose_steps(
        {"description": "goal"},
        {},
        {},
        project_key="sandbox-test-project",
    )
    assert out["ok"] is False
    assert out["reason"] == "json_parse_failed"
    print("PASS propose_steps bad JSON -> not ok")


def test_propose_health_fail():
    p = load_planner()

    def fake(op, **fields):
        if op == "health":
            return {"ok": False, "error": "down"}
        raise AssertionError("chat must not be called")

    p._gateway_call = fake
    out = p.propose_steps({"description": "g"}, {}, {}, project_key="p")
    assert out["ok"] is False
    assert "health_not_ok" in out["reason"]
    print("PASS propose_steps health fail")


def test_expand_llm_source_and_ops():
    p = load_planner()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)

    def fake(op, **fields):
        if op == "health":
            return {"ok": True}
        return {"ok": True, "text": _valid_steps_json(3)}

    p._gateway_call = fake
    path, db = make_db()
    try:
        result = p.expand_goal(
            db,
            goal_id=1,
            project="sandbox-test-project",
            planner_config={
                "mode": "llm_optional",
                "fallback_template": "improve_sandbox",
                "max_steps": 8,
                "timeout_seconds": 30,
            },
        )
        assert result["source"] == "llm"
        assert len(result["tasks"]) == 3
        ops = [
            row["operation"]
            for row in db.execute(
                "SELECT operation FROM task_execution_specs ORDER BY task_id"
            )
        ]
        assert ops == ["python_test", "python_compile", "python_test"]
        assert all(op in ("python_test", "python_compile") for op in ops)
    finally:
        db.close()
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS expand_goal source=llm with allowed ops only")


def test_expand_disallowed_op_falls_back_template():
    p = load_planner()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)

    def fake(op, **fields):
        if op == "health":
            return {"ok": True}
        return {
            "ok": True,
            "text": json.dumps({
                "steps": [
                    {
                        "role": "bad",
                        "description": "nope",
                        "operation": "rm_rf",
                    }
                ]
            }),
        }

    p._gateway_call = fake
    path, db = make_db()
    try:
        result = p.expand_goal(
            db,
            goal_id=1,
            project="sandbox-test-project",
            planner_config={
                "mode": "llm_optional",
                "fallback_template": "improve_sandbox",
                "max_steps": 8,
                "timeout_seconds": 30,
            },
        )
        assert result["source"] == "template_fallback"
        assert "bad_operation" in (result.get("fallback_reason") or "")
        assert len(result["tasks"]) == 6  # improve_sandbox
        ops = {
            row["operation"]
            for row in db.execute("SELECT operation FROM task_execution_specs")
        }
        assert ops <= {"python_test", "python_compile"}
        assert "rm_rf" not in ops
    finally:
        db.close()
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS disallowed op -> template fallback; never reaches specs")


def test_expand_health_down_template_fallback():
    p = load_planner()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)

    def fake(op, **fields):
        raise ConnectionError("socket missing")

    p._gateway_call = fake
    path, db = make_db()
    try:
        result = p.expand_goal(
            db,
            goal_id=1,
            project="sandbox-test-project",
            planner_config={
                "mode": "llm_optional",
                "fallback_template": "improve_sandbox",
                "max_steps": 8,
                "timeout_seconds": 30,
            },
        )
        assert result["source"] == "template_fallback"
        assert "health_error" in (result.get("fallback_reason") or "")
        assert len(result["tasks"]) == 6
    finally:
        db.close()
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS gateway down -> template fallback")


def test_template_only_never_calls_gateway():
    p = load_planner()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    called = []

    def fake(op, **fields):
        called.append(op)
        raise AssertionError("gateway must not be called in template_only")

    p._gateway_call = fake
    path, db = make_db()
    try:
        result = p.expand_goal(
            db,
            goal_id=1,
            project="sandbox-test-project",
            planner_config={
                "mode": "template_only",
                "fallback_template": "improve_sandbox",
                "max_steps": 8,
                "timeout_seconds": 30,
            },
        )
        assert result["source"] == "template_only"
        assert len(result["tasks"]) == 6
        assert called == []
    finally:
        db.close()
        path.unlink(missing_ok=True)
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS template_only never calls gateway")


def test_load_planner_config_from_repo():
    p = load_planner()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    try:
        cfg = p.load_planner_config()
        assert cfg["mode"] in ("llm_optional", "template_only")
        assert cfg["fallback_template"] == "improve_sandbox"
        assert cfg["max_steps"] >= 1
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS load_planner_config from repo yaml")


if __name__ == "__main__":
    print("===== ANU V0.7.2 LLM PLANNER =====")
    test_validate_steps_accepts_allowed()
    test_validate_steps_rejects_disallowed_op()
    test_propose_valid_json_returns_llm()
    test_propose_bad_json_falls_through()
    test_propose_health_fail()
    test_expand_llm_source_and_ops()
    test_expand_disallowed_op_falls_back_template()
    test_expand_health_down_template_fallback()
    test_template_only_never_calls_gateway()
    test_load_planner_config_from_repo()
    print("LLM_PLANNER_TEST_COMPLETE")
