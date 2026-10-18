#!/usr/bin/env python3
"""V0.6.1 step 4: template planner expands Goal → Plan → Task Graph."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-template-planner-v0.1.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_planner():
    planner = load(MODULE, "template_planner_v061")
    # Force sibling modules from this checkout (not /srv/anu).
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
        ("Improve sandbox project",),
    )
    db.commit()
    return path, db


def test_build_plan_and_list_templates():
    planner = load_planner()
    assert "improve_sandbox" in planner.list_templates()
    plan = planner.build_plan_dict(1, "improve_sandbox", plan_id="plan-t")
    assert plan["plan_id"] == "plan-t"
    assert len(plan["actions"]) == 6
    assert plan["actions"][0]["description"] == "inspect project"
    assert plan["actions"][1]["parameters"]["operation"] == "python_compile"
    try:
        planner.build_plan_dict(1, "nope")
        raise AssertionError("unknown template should fail")
    except ValueError:
        pass
    print("PASS build_plan_dict and list_templates")


def test_expand_goal_creates_plan_tasks_deps_specs():
    planner = load_planner()
    path, db = make_db()
    try:
        # Ensure plan table exists via plan service path used by expand.
        result = planner.expand_goal(
            db,
            goal_id=1,
            project="sandbox-test-project",
            template_name="improve_sandbox",
            changes_file="changes.json",
            plan_id="plan-expand-1",
        )
        assert result["plan"]["plan_id"] == "plan-expand-1"
        assert result["plan"]["version"] == 1
        assert len(result["tasks"]) == 6

        goal = db.execute(
            "SELECT status FROM goals WHERE id = 1"
        ).fetchone()
        assert goal["status"] == "active"

        tasks = db.execute(
            "SELECT id, goal_id, description, status FROM tasks "
            "WHERE goal_id = 1 ORDER BY id"
        ).fetchall()
        assert len(tasks) == 6
        assert all(t["status"] == "pending" for t in tasks)
        assert tasks[0]["description"] == "inspect project"

        deps = db.execute(
            "SELECT task_id, depends_on_task_id FROM task_dependencies "
            "ORDER BY task_id"
        ).fetchall()
        assert len(deps) == 5
        for i, dep in enumerate(deps):
            assert dep["depends_on_task_id"] == tasks[i]["id"]
            assert dep["task_id"] == tasks[i + 1]["id"]

        specs = db.execute(
            "SELECT task_id, project, operation FROM task_execution_specs "
            "ORDER BY task_id"
        ).fetchall()
        assert len(specs) == 6
        assert all(s["project"] == "sandbox-test-project" for s in specs)
        assert specs[0]["operation"] == "python_test"
        assert specs[1]["operation"] == "python_compile"

        # First task has no deps → READY; second waits on first.
        graph = load(REPO / "bin" / "anu-task-graph-v0.1.py", "graph_planner_test")
        first = graph.dependency_readiness(db, tasks[0]["id"])
        second = graph.dependency_readiness(db, tasks[1]["id"])
        assert first["state"] == "READY"
        assert second["state"] == "WAITING"
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS expand_goal plan/tasks/deps/specs + readiness")


def test_replan_supersedes_previous():
    planner = load_planner()
    path, db = make_db()
    try:
        first = planner.expand_goal(
            db,
            1,
            "sandbox-test-project",
            template_name="improve_sandbox",
            plan_id="plan-a",
        )
        second = planner.expand_goal(
            db,
            1,
            "sandbox-test-project",
            template_name="improve_sandbox",
            plan_id="plan-b",
        )
        assert first["plan"]["version"] == 1
        assert second["plan"]["version"] == 2
        assert second["plan"]["status"] == "active"

        plans = load(
            REPO / "bin" / "anu-goal-plan-service-v0.1.py",
            "plans_replan_test",
        )
        plans.MODEL_MODULE = REPO / "bin" / "anu-agent-model-v0.1.py"
        old = plans.get_plan(db, "plan-a")
        assert old["status"] == "superseded"
        # Second expand adds another 6 tasks (replan does not delete old).
        count = db.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE goal_id = 1"
        ).fetchone()["n"]
        assert count == 12
    finally:
        db.close()
        path.unlink(missing_ok=True)

    print("PASS replan supersedes previous plan version")


if __name__ == "__main__":
    print("===== ANU V0.6.1 TEMPLATE PLANNER REGRESSION =====")
    test_build_plan_and_list_templates()
    test_expand_goal_creates_plan_tasks_deps_specs()
    test_replan_supersedes_previous()
    print("TEMPLATE_PLANNER_TEST_COMPLETE")
