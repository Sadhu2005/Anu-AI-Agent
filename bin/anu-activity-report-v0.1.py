#!/usr/bin/env python3
"""ANU Activity Report v0.1 (V0.7.5) — “What did you do?”

Read-only assembly of goal / plan / tasks / events from the brain.
No mutations, no network.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DB_FILE = BASE / "brain" / "anu-brain.db"
_HERE = Path(__file__).resolve().parent

DEFAULT_EVENT_LIMIT = 50


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
    if hasattr(module, "BASE"):
        module.BASE = BASE
    return module


def _load_goals():
    return _load_module(
        _module_path("anu-goal-service-v0.1.py"),
        "anu_goal_service_v075_report",
    )


def _load_plans():
    return _load_module(
        _module_path("anu-goal-plan-service-v0.1.py"),
        "anu_goal_plan_service_v075_report",
    )


def _load_aggregator():
    return _load_module(
        _module_path("anu-goal-aggregator-v0.1.py"),
        "anu_goal_aggregator_v075_report",
    )


def _load_project_intelligence():
    try:
        return _load_module(
            _module_path("anu-project-intelligence-v0.1.py"),
            "anu_project_intelligence_v083_report",
        )
    except Exception:
        return None


def _dossier_best_effort(db, project_key):
    """V0.8.3: attach project dossier when intelligence is available."""
    if not isinstance(project_key, str) or not project_key.strip():
        return None
    pi = _load_project_intelligence()
    if pi is None:
        return None
    try:
        if hasattr(pi, "migrate"):
            pi.migrate(db)
        return pi.build_project_dossier(
            db, project_key.strip(), refresh=False
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _require_db(db):
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")
    return db


def db_connect(db_path=None):
    path = Path(db_path) if db_path is not None else DB_FILE
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def resolve_project_key(db, goal) -> str | None:
    """Map goals.project_id → projects.project_key when available."""
    db = _require_db(db)
    if not isinstance(goal, dict):
        return None
    project_id = goal.get("project_id")
    if project_id is None:
        return None
    try:
        row = db.execute(
            "SELECT project_key FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return row["project_key"]
    return row[0]


def _source_hint_from_plan(plan) -> str:
    if not isinstance(plan, dict):
        return "unknown"
    body = plan.get("body")
    if not isinstance(body, dict):
        return "unknown"
    actions = body.get("actions")
    if not isinstance(actions, list) or not actions:
        # plan_id naming hints
        plan_id = str(body.get("plan_id") or plan.get("plan_id") or "")
        if plan_id.startswith("plan-llm"):
            return "llm"
        if "improve_sandbox" in plan_id or plan_id.startswith("plan-"):
            return "template"
        return "unknown"
    first = actions[0] if isinstance(actions[0], dict) else {}
    params = first.get("parameters") if isinstance(first, dict) else {}
    if not isinstance(params, dict):
        return "unknown"
    if params.get("planner") == "llm" or params.get("template") == "llm":
        return "llm"
    template = params.get("template")
    if isinstance(template, str) and template.strip():
        return "template"
    return "unknown"


def _list_goal_tasks(db, goal_id):
    """Tasks with title/status/operation for a goal."""
    db = _require_db(db)
    try:
        rows = db.execute(
            """SELECT t.id, t.task_key, t.title, t.description, t.status,
                      t.attempts, t.created_at, t.updated_at,
                      s.operation, s.project AS spec_project
               FROM tasks t
               LEFT JOIN task_execution_specs s ON s.task_id = t.id
               WHERE t.goal_id = ?
               ORDER BY t.id ASC""",
            (goal_id,),
        ).fetchall()
    except sqlite3.Error:
        # Minimal fallback via aggregator statuses only.
        agg = _load_aggregator()
        return [
            {
                "id": item["id"],
                "title": None,
                "status": item["status"],
                "attempts": item.get("attempts"),
                "operation": None,
            }
            for item in agg.list_goal_task_statuses(db, goal_id)
        ]

    out = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            row = dict(row)
        out.append({
            "id": row["id"],
            "task_key": row.get("task_key"),
            "title": row.get("title"),
            "description": row.get("description"),
            "status": row.get("status"),
            "attempts": row.get("attempts"),
            "operation": row.get("operation"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        })
    return out


def _list_goal_events(db, goal_id, *, limit=DEFAULT_EVENT_LIMIT):
    db = _require_db(db)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    try:
        rows = db.execute(
            """SELECT id, event_type, message, metadata, created_at, task_id
               FROM agent_events
               WHERE goal_id = ?
               ORDER BY id DESC
               LIMIT ?""",
            (goal_id, limit),
        ).fetchall()
    except sqlite3.Error:
        return []

    out = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            row = dict(row)
        out.append({
            "id": row["id"],
            "event_type": row.get("event_type"),
            "message": row.get("message"),
            "created_at": row.get("created_at"),
            "task_id": row.get("task_id"),
        })
    return out


def _summary_from_tasks(tasks, events):
    total = len(tasks)
    completed = sum(
        1 for t in tasks
        if str(t.get("status") or "").lower() in ("completed", "complete", "done")
    )
    failed = sum(
        1 for t in tasks
        if str(t.get("status") or "").lower() in (
            "failed", "failure", "error", "rolled_back", "cancelled", "canceled"
        )
    )
    last_event_at = None
    if events:
        last_event_at = events[0].get("created_at")
    return {
        "task_total": total,
        "task_completed": completed,
        "task_failed": failed,
        "last_event_at": last_event_at,
    }


def report_goal(db, goal_id, *, event_limit=DEFAULT_EVENT_LIMIT):
    """Build a single-goal activity report. Returns dict with ok True/False."""
    db = _require_db(db)
    if isinstance(goal_id, bool) or not isinstance(goal_id, int) or goal_id < 1:
        return {"ok": False, "error": "goal_id must be a positive integer"}

    goals = _load_goals()
    plans = _load_plans()
    if hasattr(plans, "migrate"):
        try:
            plans.migrate(db)
        except Exception:
            pass

    goal = goals.get_goal(db, goal_id)
    if goal is None:
        return {"ok": False, "error": f"goal not found: {goal_id}"}

    project_key = resolve_project_key(db, goal)
    active = None
    try:
        active = plans.get_active_plan(db, goal_id)
    except Exception:
        active = None
    if active is None:
        try:
            listed = plans.list_plans(db, goal_id, limit=1)
            active = listed[0] if listed else None
        except Exception:
            active = None

    plan_block = None
    if isinstance(active, dict):
        body = active.get("body") if isinstance(active.get("body"), dict) else {}
        actions = body.get("actions") if isinstance(body, dict) else None
        action_count = len(actions) if isinstance(actions, list) else 0
        plan_block = {
            "plan_id": active.get("plan_id"),
            "version": active.get("version"),
            "status": active.get("status"),
            "source_hint": _source_hint_from_plan(active),
            "action_count": action_count,
            "created_at": active.get("created_at"),
        }

    tasks = _list_goal_tasks(db, goal_id)
    events = _list_goal_events(db, goal_id, limit=event_limit)
    project_dossier = _dossier_best_effort(db, project_key)

    return {
        "ok": True,
        "goal": {
            "id": goal.get("id"),
            "status": goal.get("status"),
            "description": goal.get("description"),
            "project_id": goal.get("project_id"),
            "project_key": project_key,
            "created_at": goal.get("created_at"),
        },
        "plan": plan_block,
        "tasks": tasks,
        "events": events,
        "summary": _summary_from_tasks(tasks, events),
        "project_dossier": project_dossier,
    }


def report_project(db, project_key, *, event_limit=DEFAULT_EVENT_LIMIT):
    """V0.8.3: project-level activity snapshot via dossier + recent goals."""
    db = _require_db(db)
    if not isinstance(project_key, str) or not project_key.strip():
        return {"ok": False, "error": "project_key must be a non-empty string"}
    project_key = project_key.strip()
    dossier = _dossier_best_effort(db, project_key)
    if dossier is None:
        return {
            "ok": False,
            "error": "project intelligence unavailable",
            "project_key": project_key,
        }
    if isinstance(dossier, dict) and dossier.get("ok") is False:
        return {
            "ok": False,
            "error": dossier.get("error") or "dossier failed",
            "project_key": project_key,
        }

    recent = []
    for gid in list(dossier.get("recent_goal_ids") or [])[:5]:
        try:
            recent.append(report_goal(db, gid, event_limit=event_limit))
        except Exception as exc:
            recent.append({
                "ok": False,
                "goal_id": gid,
                "error": f"{type(exc).__name__}: {exc}",
            })

    return {
        "ok": True,
        "project_key": project_key,
        "dossier": dossier,
        "recent_goals": recent,
        "summary": {
            "open_goals": dossier.get("open_goals"),
            "open_tasks": dossier.get("open_tasks"),
            "verification_hint_count": dossier.get("verification_hint_count"),
            "memory_repeated_failure": dossier.get("memory_repeated_failure"),
            "recent_goal_count": len(recent),
        },
    }


def report_recent(db, *, limit=5, event_limit=DEFAULT_EVENT_LIMIT):
    """Reports for the newest goals (newest first)."""
    db = _require_db(db)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        return {"ok": False, "error": "limit must be a positive integer"}

    goals = _load_goals()
    listed = goals.list_goals(db, limit=limit)
    reports = []
    for goal in listed:
        gid = goal.get("id")
        if not isinstance(gid, int):
            continue
        reports.append(report_goal(db, gid, event_limit=event_limit))

    return {
        "ok": True,
        "count": len(reports),
        "reports": reports,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ANU Activity Report v0.1 — What did you do?"
    )
    parser.add_argument("--db", default=str(DB_FILE))
    parser.add_argument(
        "--event-limit",
        type=int,
        default=DEFAULT_EVENT_LIMIT,
        help="Max agent_events rows per goal (default 50)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    goal_p = sub.add_parser("goal", help="Report one goal by id")
    goal_p.add_argument("--goal-id", type=int, required=True)

    recent_p = sub.add_parser("recent", help="Report newest goals")
    recent_p.add_argument("--limit", type=int, default=5)

    project_p = sub.add_parser(
        "project",
        help="Project dossier + recent linked goals (V0.8.3)",
    )
    project_p.add_argument("--project-key", required=True)

    args = parser.parse_args(argv)
    db = db_connect(args.db)
    try:
        if args.cmd == "goal":
            result = report_goal(
                db,
                args.goal_id,
                event_limit=args.event_limit,
            )
            print(json.dumps(result, indent=2, default=str))
            return 0 if result.get("ok") else 1
        if args.cmd == "recent":
            result = report_recent(
                db,
                limit=args.limit,
                event_limit=args.event_limit,
            )
            print(json.dumps(result, indent=2, default=str))
            return 0 if result.get("ok") else 1
        if args.cmd == "project":
            result = report_project(
                db,
                args.project_key,
                event_limit=args.event_limit,
            )
            print(json.dumps(result, indent=2, default=str))
            return 0 if result.get("ok") else 1
        parser.error(f"unknown command: {args.cmd}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
