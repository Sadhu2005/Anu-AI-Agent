#!/usr/bin/env python3
"""ANU Goal Recovery v0.1 (V0.6.1 step 6).

When a goal's task graph fails, either:

- **block** the goal with a recorded reason (and retire open tasks), or
- **replan** the goal (retire open tasks, expand a new template plan version).

Does not invent schema. Uses goals, tasks, goal_plans, task_execution_specs,
and agent_events already present.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/srv/anu")
DB_FILE = BASE / "brain" / "anu-brain.db"
_HERE = Path(__file__).resolve().parent

OPEN_TASK_STATUSES = frozenset({
    "pending",
    "ready",
    "planning",
    "running",
    "verifying",
    "failed",
})

POLICIES = frozenset({"block", "replan"})


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


def _load(name, module_name):
    path = _module_path(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_goal_service():
    return _load("anu-goal-service-v0.1.py", "anu_goal_service_v0_1_recovery")


def _load_plan_service():
    return _load(
        "anu-goal-plan-service-v0.1.py",
        "anu_goal_plan_service_v0_1_recovery",
    )


def _load_planner():
    return _load(
        "anu-template-planner-v0.1.py",
        "anu_template_planner_v0_1_recovery",
    )


def _emit_goal_event(db, goal_id, event_type, message, metadata=None):
    metadata = metadata or {}
    try:
        db.execute(
            """INSERT INTO agent_events
                   (task_id, goal_id, parent_task_id, run_id, attempt,
                    event_type, message, metadata, created_at)
               VALUES (NULL, ?, NULL, NULL, NULL, ?, ?, ?, ?)""",
            (
                goal_id,
                event_type,
                message,
                json.dumps(metadata, sort_keys=True),
                now_iso(),
            ),
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _validate_goal_id(goal_id):
    if isinstance(goal_id, bool) or not isinstance(goal_id, int) or goal_id < 1:
        raise ValueError("goal_id must be a positive integer")
    return goal_id


def _validate_reason(reason):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a non-empty string")
    return reason.strip()


def retire_open_tasks(db, goal_id, reason, failure_class="contract"):
    """Block non-terminal tasks for a goal so a replan/block is unambiguous."""
    db = _require_db(db)
    goal_id = _validate_goal_id(goal_id)
    reason = _validate_reason(reason)

    # failure_class column may exist (V0.6.0). Try with it; fall back without.
    rows = db.execute(
        """SELECT id, status FROM tasks
           WHERE goal_id = ? AND status IN ({})
           ORDER BY id""".format(
            ",".join(f"'{s}'" for s in sorted(OPEN_TASK_STATUSES))
        ),
        (goal_id,),
    ).fetchall()

    retired = []
    updated_at = now_iso()
    for row in rows:
        task_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        prior = row["status"] if isinstance(row, sqlite3.Row) else row[1]
        try:
            db.execute(
                """UPDATE tasks
                   SET status = 'blocked',
                       failure_class = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (failure_class, updated_at, task_id),
            )
        except sqlite3.OperationalError:
            db.execute(
                """UPDATE tasks
                   SET status = 'blocked', updated_at = ?
                   WHERE id = ?""",
                (updated_at, task_id),
            )
        retired.append({"task_id": task_id, "from_status": prior})

    db.commit()

    for item in retired:
        _emit_goal_event(
            db,
            goal_id,
            "TASK_BLOCKED",
            f"retired open task {item['task_id']}: {reason}",
            {
                "task_id": item["task_id"],
                "from_status": item["from_status"],
                "reason": reason,
                "failure_class": failure_class,
                "source": "goal_recovery",
            },
        )

    return retired


def _infer_execution_context(db, goal_id):
    """Best-effort project/changes_file/template from existing rows."""
    project = None
    changes_file = None
    template_name = None

    row = db.execute(
        """SELECT tes.project, tes.changes_file
           FROM task_execution_specs tes
           JOIN tasks t ON t.id = tes.task_id
           WHERE t.goal_id = ?
           ORDER BY t.id DESC
           LIMIT 1""",
        (goal_id,),
    ).fetchone()
    if row is not None:
        project = row["project"] if isinstance(row, sqlite3.Row) else row[0]
        changes_file = (
            row["changes_file"] if isinstance(row, sqlite3.Row) else row[1]
        )

    plans = _load_plan_service()
    plans.migrate(db)
    active = plans.get_active_plan(db, goal_id)
    if active is not None:
        body = active.get("body") or {}
        actions = body.get("actions") or []
        if actions:
            params = actions[0].get("parameters") or {}
            template_name = params.get("template")

    return {
        "project": project,
        "changes_file": changes_file,
        "template_name": template_name,
        "active_plan_id": None if active is None else active["plan_id"],
    }


def block_goal(db, goal_id, reason, task_id=None, failure_class="contract"):
    """Retire open tasks and mark the goal blocked with a reason."""
    db = _require_db(db)
    goal_id = _validate_goal_id(goal_id)
    reason = _validate_reason(reason)

    goals = _load_goal_service()
    goal = goals.get_goal(db, goal_id)
    if goal is None:
        raise ValueError(f"goal does not exist: goal_id={goal_id}")

    retired = retire_open_tasks(
        db, goal_id, reason, failure_class=failure_class
    )
    updated = goals.update_goal_status(
        db,
        goal_id,
        "blocked",
        reason=reason,
    )
    _emit_goal_event(
        db,
        goal_id,
        "GOAL_BLOCKED",
        f"goal {goal_id} blocked: {reason}",
        {
            "goal_id": goal_id,
            "reason": reason,
            "task_id": task_id,
            "retired_task_ids": [r["task_id"] for r in retired],
            "policy": "block",
        },
    )
    return {
        "policy": "block",
        "goal": updated,
        "reason": reason,
        "task_id": task_id,
        "retired": retired,
    }


def replan_goal(
    db,
    goal_id,
    reason,
    *,
    task_id=None,
    template_name=None,
    project=None,
    changes_file=None,
    plan_id=None,
):
    """Retire open tasks and expand a new plan version for the goal."""
    db = _require_db(db)
    goal_id = _validate_goal_id(goal_id)
    reason = _validate_reason(reason)

    goals = _load_goal_service()
    goal = goals.get_goal(db, goal_id)
    if goal is None:
        raise ValueError(f"goal does not exist: goal_id={goal_id}")

    inferred = _infer_execution_context(db, goal_id)
    template_name = template_name or inferred["template_name"] or "improve_sandbox"
    project = project or inferred["project"]
    changes_file = changes_file or inferred["changes_file"] or "changes.json"

    if not project:
        raise ValueError(
            "project is required for replan (none on goal tasks and not provided)"
        )

    retired = retire_open_tasks(
        db, goal_id, f"replan: {reason}", failure_class="contract"
    )

    planner = _load_planner()
    expansion = planner.expand_goal(
        db,
        goal_id,
        project=project,
        template_name=template_name,
        changes_file=changes_file,
        plan_id=plan_id,
    )

    _emit_goal_event(
        db,
        goal_id,
        "GOAL_REPLANNED",
        f"goal {goal_id} replanned: {reason}",
        {
            "goal_id": goal_id,
            "reason": reason,
            "task_id": task_id,
            "retired_task_ids": [r["task_id"] for r in retired],
            "new_plan_id": expansion["plan"]["plan_id"],
            "new_plan_version": expansion["plan"]["version"],
            "template": template_name,
            "policy": "replan",
        },
    )

    return {
        "policy": "replan",
        "goal": goals.get_goal(db, goal_id),
        "reason": reason,
        "task_id": task_id,
        "retired": retired,
        "expansion": expansion,
    }


def handle_goal_failure(
    db,
    goal_id,
    reason,
    *,
    policy="block",
    task_id=None,
    template_name=None,
    project=None,
    changes_file=None,
    plan_id=None,
):
    """Dispatch block or replan for a failed goal graph node."""
    if policy not in POLICIES:
        raise ValueError(
            f"invalid policy: {policy!r}; allowed={sorted(POLICIES)}"
        )
    if policy == "block":
        return block_goal(db, goal_id, reason, task_id=task_id)
    return replan_goal(
        db,
        goal_id,
        reason,
        task_id=task_id,
        template_name=template_name,
        project=project,
        changes_file=changes_file,
        plan_id=plan_id,
    )


def handle_task_failure(db, task_id, reason, *, policy="block", **kwargs):
    """Resolve the task's goal and apply the failure policy."""
    db = _require_db(db)
    if isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1:
        raise ValueError("task_id must be a positive integer")

    row = db.execute(
        "SELECT id, goal_id, status FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"task does not exist: task_id={task_id}")

    goal_id = row["goal_id"] if isinstance(row, sqlite3.Row) else row[1]
    if goal_id is None:
        raise ValueError(
            f"task {task_id} has no goal_id; cannot apply goal recovery"
        )

    return handle_goal_failure(
        db,
        goal_id,
        reason,
        policy=policy,
        task_id=task_id,
        **kwargs,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Goal Recovery v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)

    block_p = sub.add_parser("block")
    block_p.add_argument("--goal-id", type=int, required=True)
    block_p.add_argument("--reason", required=True)
    block_p.add_argument("--task-id", type=int, default=None)

    replan_p = sub.add_parser("replan")
    replan_p.add_argument("--goal-id", type=int, required=True)
    replan_p.add_argument("--reason", required=True)
    replan_p.add_argument("--task-id", type=int, default=None)
    replan_p.add_argument("--template", default=None)
    replan_p.add_argument("--project", default=None)
    replan_p.add_argument("--changes-file", default=None)

    task_p = sub.add_parser("from-task")
    task_p.add_argument("--task-id", type=int, required=True)
    task_p.add_argument("--reason", required=True)
    task_p.add_argument("--policy", choices=sorted(POLICIES), default="block")
    task_p.add_argument("--template", default=None)
    task_p.add_argument("--project", default=None)
    task_p.add_argument("--changes-file", default=None)

    args = parser.parse_args(argv)
    db = db_connect(args.db)
    try:
        if args.cmd == "block":
            result = block_goal(
                db, args.goal_id, args.reason, task_id=args.task_id
            )
        elif args.cmd == "replan":
            result = replan_goal(
                db,
                args.goal_id,
                args.reason,
                task_id=args.task_id,
                template_name=args.template,
                project=args.project,
                changes_file=args.changes_file,
            )
        elif args.cmd == "from-task":
            result = handle_task_failure(
                db,
                args.task_id,
                args.reason,
                policy=args.policy,
                template_name=args.template,
                project=args.project,
                changes_file=args.changes_file,
            )
        else:
            parser.error(f"unknown command: {args.cmd}")
            return 2
        print(json.dumps({"ok": True, "result": result}, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
