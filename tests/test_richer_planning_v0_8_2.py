#!/usr/bin/env python3
"""V0.8.2: multi_stage_goal template + default expand graph."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-template-planner-v0.1.py"
LLM_MOD = REPO / "bin" / "anu-llm-planner-v0.1.py"

STAGES = ("research", "design", "implement", "test", "verify")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_planner():
    planner = load(MOD, "tpl_v082")
    planner.BASE = REPO
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
        ("Ship multi-stage goal",),
    )
    db.commit()
    return path, db


def test_template_listed_and_default():
    planner = load_planner()
    names = planner.list_templates()
    assert "multi_stage_goal" in names, names
    assert "improve_sandbox" in names, names
    assert planner.DEFAULT_TEMPLATE == "multi_stage_goal"
    llm = load(LLM_MOD, "llm_v082")
    assert llm.DEFAULT_FALLBACK_TEMPLATE == "multi_stage_goal"
    assert "research, design, implement, test, verify" in llm.SYSTEM_PROMPT
    print("PASS template listed + defaults + LLM prompt nudge")


def test_multi_stage_expand_roles_and_deps():
    planner = load_planner()
    path, db = make_db()
    try:
        result = planner.expand_goal(
            db,
            goal_id=1,
            project="sandbox-test-project",
            changes_file="changes.json",
            plan_id="plan-multi-1",
        )
        assert result["template"] == "multi_stage_goal", result
        assert len(result["tasks"]) == 5, result

        plan = planner.build_plan_dict(1, "multi_stage_goal", plan_id="x")
        roles = [a["parameters"]["role"] for a in plan["actions"]]
        assert roles == list(STAGES), roles
        ops = [a["parameters"]["operation"] for a in plan["actions"]]
        assert ops == [
            "python_compile",
            "python_compile",
            "python_test",
            "python_test",
            "python_test",
        ], ops

        tasks = db.execute(
            "SELECT id, title, description FROM tasks "
            "WHERE goal_id = 1 ORDER BY id"
        ).fetchall()
        assert len(tasks) == 5
        for task, role in zip(tasks, STAGES):
            assert role in task["title"], (task["title"], role)

        deps = db.execute(
            "SELECT task_id, depends_on_task_id FROM task_dependencies "
            "ORDER BY task_id"
        ).fetchall()
        assert len(deps) == 4
        for i, dep in enumerate(deps):
            assert dep["depends_on_task_id"] == tasks[i]["id"]
            assert dep["task_id"] == tasks[i + 1]["id"]
        print("PASS multi_stage_goal expand: 5 roles + linear deps")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def test_improve_sandbox_still_works():
    planner = load_planner()
    path, db = make_db()
    try:
        result = planner.expand_goal(
            db,
            goal_id=1,
            project="sandbox-test-project",
            template_name="improve_sandbox",
            plan_id="plan-sandbox-1",
        )
        assert result["template"] == "improve_sandbox"
        assert len(result["tasks"]) == 6
        print("PASS improve_sandbox still expands")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def main():
    test_template_listed_and_default()
    test_multi_stage_expand_roles_and_deps()
    test_improve_sandbox_still_works()
    print("ALL V0.8.2 richer planning tests PASS")


if __name__ == "__main__":
    main()
