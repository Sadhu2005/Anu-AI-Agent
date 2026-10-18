#!/usr/bin/env python3
"""ANU Template Planner v0.1 (V0.6.1 step 4).

Rule/template-based Goal → Plan → Task Graph expansion.

- Builds an ``AgentPlan`` from a named template (no model gateway required).
- Persists the plan via ``anu-goal-plan-service-v0.1``.
- Expands each action into a Task Engine task, a linear dependency edge,
  and a ``task_execution_specs`` row using existing operations only
  (``python_test`` / ``python_compile``).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/srv/anu")
DB_FILE = BASE / "brain" / "anu-brain.db"
_HERE = Path(__file__).resolve().parent

ALLOWED_OPERATIONS = frozenset({"python_test", "python_compile"})

# Named templates. Every step maps to an existing Safe Mutation operation.
# Roles are recorded in AgentAction.parameters for later planners; execution
# still goes through python_test / python_compile only.
TEMPLATES = {
    "improve_sandbox": [
        {
            "action_id": "inspect",
            "kind": "TASK",
            "description": "inspect project",
            "operation": "python_test",
            "role": "inspect",
        },
        {
            "action_id": "identify",
            "kind": "TASK",
            "description": "identify issue",
            "operation": "python_compile",
            "role": "identify",
        },
        {
            "action_id": "propose",
            "kind": "TASK",
            "description": "propose modification",
            "operation": "python_test",
            "role": "propose",
        },
        {
            "action_id": "test",
            "kind": "TASK",
            "description": "test modification",
            "operation": "python_test",
            "role": "test",
        },
        {
            "action_id": "apply",
            "kind": "TASK",
            "description": "apply modification",
            "operation": "python_test",
            "role": "apply",
        },
        {
            "action_id": "verify",
            "kind": "TASK",
            "description": "verify final state",
            "operation": "python_test",
            "role": "verify",
        },
    ],
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db_connect(db_path=None):
    path = Path(db_path) if db_path is not None else DB_FILE
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _require_db(db):
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")
    return db


def _module_path(name):
    live = BASE / "bin" / name
    if live.is_file():
        return live
    return _HERE / name


def _load_module(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_goal_service():
    return _load_module(
        _module_path("anu-goal-service-v0.1.py"),
        "anu_goal_service_v0_1_planner",
    )


def _load_plan_service():
    return _load_module(
        _module_path("anu-goal-plan-service-v0.1.py"),
        "anu_goal_plan_service_v0_1_planner",
    )


def _load_task_graph():
    return _load_module(
        _module_path("anu-task-graph-v0.1.py"),
        "anu_task_graph_v0_1_planner",
    )


def _load_execution_spec():
    return _load_module(
        _module_path("anu-task-execution-spec-v0.1.py"),
        "anu_task_execution_spec_v0_1_planner",
    )


def list_templates():
    return sorted(TEMPLATES.keys())


def build_plan_dict(goal_id, template_name, plan_id=None):
    """Build an AgentPlan-compatible dict from a template."""
    if template_name not in TEMPLATES:
        raise ValueError(
            f"unknown template: {template_name!r}; "
            f"allowed={list_templates()}"
        )
    if isinstance(goal_id, bool) or not isinstance(goal_id, int) or goal_id < 1:
        raise ValueError("goal_id must be a positive integer")

    goal_key = str(goal_id)
    stamp = uuid.uuid4().hex[:10]
    plan_id = plan_id or f"plan-{template_name}-{stamp}"
    actions = []
    for step in TEMPLATES[template_name]:
        operation = step["operation"]
        if operation not in ALLOWED_OPERATIONS:
            raise ValueError(
                f"template step {step['action_id']!r} uses unsupported "
                f"operation {operation!r}"
            )
        actions.append({
            "action_id": f"{step['action_id']}-{stamp}",
            "goal_id": goal_key,
            "kind": step["kind"],
            "description": step["description"],
            "parameters": {
                "template": template_name,
                "role": step["role"],
                "operation": operation,
            },
        })
    return {
        "plan_id": plan_id,
        "goal_id": goal_key,
        "actions": actions,
    }


def _create_task_row(
    db,
    *,
    goal_id,
    title,
    description,
    priority,
    task_key,
):
    created = now_iso()
    cur = db.execute(
        """INSERT INTO tasks(
               goal_id, parent_task_id, task_key, title, description,
               status, priority, attempts, created_at, updated_at
           ) VALUES (?, NULL, ?, ?, ?, 'pending', ?, 0, ?, ?)""",
        (
            goal_id,
            task_key,
            title,
            description,
            priority,
            created,
            created,
        ),
    )
    db.commit()
    return cur.lastrowid


def _load_observation_memory():
    return _load_module(
        _module_path("anu-observation-memory-v0.1.py"),
        "anu_observation_memory_v0_1_planner",
    )


def _load_project_intelligence():
    return _load_module(
        _module_path("anu-project-intelligence-v0.1.py"),
        "anu_project_intelligence_v0_1_planner",
    )


def _memory_hints_for_project(db, project):
    """Best-effort V0.6.3 retrieval; never fails planning."""
    try:
        mem = _load_observation_memory()
        if hasattr(mem, "migrate"):
            mem.migrate(db)
        return mem.retrieve_for_planning(db, project_key=project, limit=5)
    except Exception:
        return {
            "project_key": project,
            "memories": [],
            "repeated_failure": False,
            "failure_classes": [],
            "hints": [],
        }


def _project_context_for_planning(db, project):
    """V0.6.4: require registration and attach project model summary."""
    pi = _load_project_intelligence()
    # Fail closed on unregistered projects.
    pi.require_registered(project)
    try:
        return pi.planner_project_context(db, project, refresh=True)
    except Exception as exc:
        # Registration already passed; model refresh is best-effort.
        return {
            "project_key": project,
            "registered": True,
            "error": str(exc),
            "file_count": None,
            "has_tests": None,
            "known_problem_count": 0,
            "repeated_failure": False,
            "top_level": [],
            "hints": [],
        }


def expand_goal(
    db,
    goal_id,
    project,
    template_name="improve_sandbox",
    changes_file="changes.json",
    plan_id=None,
):
    """Create plan + task graph for a goal from a template.

    Returns a summary dict with plan record and created task ids.
    """
    db = _require_db(db)

    if not isinstance(project, str) or not project.strip():
        raise ValueError("project must be a non-empty string")
    if not isinstance(changes_file, str) or not changes_file.strip():
        raise ValueError("changes_file must be a non-empty string")

    project = project.strip()
    changes_file = changes_file.strip()

    goals = _load_goal_service()
    plans = _load_plan_service()
    graph = _load_task_graph()
    specs = _load_execution_spec()

    # Ensure plan storage exists (idempotent).
    plans.migrate(db)
    if hasattr(specs, "migrate"):
        specs.migrate(db)

    goal = goals.get_goal(db, goal_id)
    if goal is None:
        raise ValueError(f"goal does not exist: goal_id={goal_id}")

    project_ctx = _project_context_for_planning(db, project)
    memory = _memory_hints_for_project(db, project)
    plan_dict = build_plan_dict(goal_id, template_name, plan_id=plan_id)

    # V0.6.4: planner decisions reference project state.
    for action in plan_dict["actions"]:
        action["parameters"]["project_registered"] = True
        action["parameters"]["project_file_count"] = project_ctx.get("file_count")
        action["parameters"]["project_has_tests"] = project_ctx.get("has_tests")
        action["parameters"]["project_known_problems"] = project_ctx.get(
            "known_problem_count"
        )
        if project_ctx.get("top_level"):
            action["parameters"]["project_top_level"] = list(
                project_ctx["top_level"]
            )[:20]

    # V0.6.3: repeated failures from memory change the planned parameters.
    if memory.get("hints"):
        for action in plan_dict["actions"]:
            action["parameters"]["memory_hints"] = list(memory["hints"])
            action["parameters"]["memory_repeated_failure"] = bool(
                memory.get("repeated_failure")
            )
            if memory.get("failure_classes"):
                action["parameters"]["memory_failure_classes"] = list(
                    memory["failure_classes"]
                )
        if memory.get("repeated_failure") and plan_dict["actions"]:
            first = plan_dict["actions"][0]
            first["description"] = (
                f"{first['description']} "
                f"[memory: repeated failure "
                f"{','.join(memory.get('failure_classes') or ['unknown'])}]"
            )

    plan_record = plans.save_plan(db, goal_id, plan_dict, status="active")

    created = []
    previous_task_id = None
    # Higher priority first so discover_ready_tasks prefers earlier steps
    # when multiple pending tasks share a goal; deps still gate admission.
    base_priority = 50

    for index, action in enumerate(plan_dict["actions"]):
        operation = action["parameters"]["operation"]
        role = action["parameters"]["role"]
        task_key = f"GOAL-{goal_id}-{role}-{uuid.uuid4().hex[:8].upper()}"
        title = f"{template_name}:{role}"
        task_id = _create_task_row(
            db,
            goal_id=goal_id,
            title=title,
            description=action["description"],
            priority=base_priority - index,
            task_key=task_key,
        )
        specs.save_execution_descriptor(
            db,
            task_id,
            project=project,
            changes_file=changes_file,
            operation=operation,
        )
        if previous_task_id is not None:
            graph.add_dependency(db, task_id, previous_task_id)

        created.append({
            "task_id": task_id,
            "task_key": task_key,
            "action_id": action["action_id"],
            "role": role,
            "operation": operation,
            "depends_on_task_id": previous_task_id,
        })
        previous_task_id = task_id

    goals.update_goal_status(
        db,
        goal_id,
        "active",
        reason=f"template {template_name} expanded to {len(created)} tasks",
    )

    return {
        "goal_id": goal_id,
        "template": template_name,
        "project": project,
        "changes_file": changes_file,
        "plan": plan_record,
        "tasks": created,
        "memory": memory,
        "project": project_ctx,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Template Planner v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("templates")

    expand_p = sub.add_parser("expand")
    expand_p.add_argument("--goal-id", type=int, required=True)
    expand_p.add_argument("--project", required=True)
    expand_p.add_argument("--template", default="improve_sandbox")
    expand_p.add_argument("--changes-file", default="changes.json")
    expand_p.add_argument("--plan-id", default=None)

    args = parser.parse_args(argv)
    if args.cmd == "templates":
        print(json.dumps({"ok": True, "templates": list_templates()}, indent=2))
        return 0

    db = db_connect(args.db)
    try:
        if args.cmd == "expand":
            result = expand_goal(
                db,
                args.goal_id,
                project=args.project,
                template_name=args.template,
                changes_file=args.changes_file,
                plan_id=args.plan_id,
            )
            print(json.dumps({"ok": True, "result": result}, indent=2))
            return 0
        parser.error(f"unknown command: {args.cmd}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
