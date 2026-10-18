#!/usr/bin/env python3
"""ANU Goal Cycle v0.1 (V0.7.0+) — glue Goal → Plan → Sync into the runtime.

Integration only: calls planner (LLM optional from V0.7.2, else template)
+ goal aggregator. Does not invent a new scheduler.
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


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "BASE"):
        module.BASE = BASE
    return module


def _load_goals():
    return _load("anu_goal_service_v070", _HERE / "anu-goal-service-v0.1.py")


def _load_plans():
    return _load(
        "anu_goal_plan_service_v070",
        _HERE / "anu-goal-plan-service-v0.1.py",
    )


def _planner_mode() -> str:
    """Read planner.mode from model-gateway config (default llm_optional)."""
    for candidate in (
        BASE / "config" / "anu-model-gateway.yaml",
        _HERE.parent / "config" / "anu-model-gateway.yaml",
    ):
        if not candidate.is_file():
            continue
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            mode = ((data.get("planner") or {}).get("mode"))
            if mode in ("llm_optional", "template_only"):
                return mode
        except Exception:
            pass
    return "llm_optional"


def _load_planner():
    """V0.7.2: LLM planner when mode=llm_optional; else template."""
    if _planner_mode() == "llm_optional":
        path = _HERE / "anu-llm-planner-v0.1.py"
        if path.is_file():
            return _load("anu_llm_planner_v072", path)
    return _load(
        "anu_template_planner_v070",
        _HERE / "anu-template-planner-v0.1.py",
    )


def _load_aggregator():
    return _load(
        "anu_goal_aggregator_v070",
        _HERE / "anu-goal-aggregator-v0.1.py",
    )


def _load_memory():
    return _load(
        "anu_observation_memory_v070",
        _HERE / "anu-observation-memory-v0.1.py",
    )


def _require_db(db):
    if not isinstance(db, sqlite3.Connection):
        raise TypeError("db must be a sqlite3.Connection")
    return db


def resolve_project_key(db, goal) -> str | None:
    """Map goals.project_id → projects.project_key."""
    db = _require_db(db)
    if not isinstance(goal, dict):
        return None
    project_id = goal.get("project_id")
    if project_id is None:
        return None
    row = db.execute(
        "SELECT project_key FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    return row[0] if not isinstance(row, sqlite3.Row) else row["project_key"]


def goal_already_expanded(db, goal_id) -> bool:
    """True when an active plan or any tasks already exist for the goal."""
    db = _require_db(db)
    plans = _load_plans()
    if hasattr(plans, "migrate"):
        plans.migrate(db)
    active = plans.get_active_plan(db, goal_id)
    if active is not None:
        return True
    count = db.execute(
        "SELECT COUNT(*) FROM tasks WHERE goal_id = ?",
        (goal_id,),
    ).fetchone()[0]
    return int(count) > 0


def list_pending_goals_oldest_first(db, limit=50):
    """Pending goals in creation order (FIFO for next-goal selection)."""
    db = _require_db(db)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    rows = db.execute(
        """SELECT id, project_id, description, status, created_at
           FROM goals
           WHERE status = 'pending'
           ORDER BY id ASC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    goals = _load_goals()
    out = []
    for row in rows:
        if hasattr(goals, "_row_to_goal"):
            out.append(goals._row_to_goal(row))
        else:
            out.append({
                "id": row[0] if not isinstance(row, sqlite3.Row) else row["id"],
                "project_id": (
                    row[1] if not isinstance(row, sqlite3.Row)
                    else row["project_id"]
                ),
                "description": (
                    row[2] if not isinstance(row, sqlite3.Row)
                    else row["description"]
                ),
                "status": (
                    row[3] if not isinstance(row, sqlite3.Row)
                    else row["status"]
                ),
                "created_at": (
                    row[4] if not isinstance(row, sqlite3.Row)
                    else row["created_at"]
                ),
            })
    return out


def expand_pending_goals(
    db,
    *,
    max_goals=1,
    template_name="improve_sandbox",
    changes_file="changes.json",
):
    """Expand up to max_goals pending goals that are not yet planned.

    Skips goals without a resolvable registered project. Idempotent: already
    expanded goals are left alone.
    """
    db = _require_db(db)
    if not isinstance(max_goals, int) or isinstance(max_goals, bool) or max_goals < 1:
        raise ValueError("max_goals must be a positive integer")

    planner = _load_planner()
    pending = list_pending_goals_oldest_first(db, limit=max(50, max_goals * 5))
    expanded = []
    skipped = []

    for goal in pending:
        if len(expanded) >= max_goals:
            break
        goal_id = goal["id"]
        if goal_already_expanded(db, goal_id):
            skipped.append({
                "goal_id": goal_id,
                "reason": "already_expanded",
            })
            continue
        project_key = resolve_project_key(db, goal)
        if not project_key:
            skipped.append({
                "goal_id": goal_id,
                "reason": "no_project",
            })
            continue
        try:
            result = planner.expand_goal(
                db,
                goal_id,
                project=project_key,
                template_name=template_name,
                changes_file=changes_file,
            )
            expanded.append({
                "goal_id": goal_id,
                "project": project_key,
                "task_count": len(result.get("tasks") or []),
                "template": result.get("template") or template_name,
                "source": result.get("source") or "template",
            })
        except Exception as exc:
            skipped.append({
                "goal_id": goal_id,
                "reason": f"{type(exc).__name__}: {exc}",
            })

    return {
        "expanded": expanded,
        "skipped": skipped,
        "expanded_count": len(expanded),
        "skipped_count": len(skipped),
    }


def remember_completed_goals(db, sync_results):
    """Write observation + memory for goals that just became completed."""
    db = _require_db(db)
    if not sync_results:
        return []

    try:
        memory = _load_memory()
    except Exception:
        return []

    if hasattr(memory, "migrate"):
        try:
            memory.migrate(db)
        except Exception:
            pass

    remembered = []
    for summary in sync_results:
        if not summary.get("changed"):
            continue
        after = summary.get("after") or (summary.get("goal") or {}).get("status")
        derived = summary.get("derived")
        if after != "completed" and derived != "completed":
            continue
        goal_id = summary.get("goal_id")
        if not isinstance(goal_id, int):
            continue

        goal = summary.get("goal") or {}
        project_key = resolve_project_key(db, goal) if goal else None
        if project_key is None and goal.get("project_id") is not None:
            project_key = resolve_project_key(
                db,
                {
                    "project_id": goal.get("project_id"),
                },
            )

        content = (
            f"goal {goal_id} completed "
            f"({summary.get('task_count', 0)} tasks)"
        )
        try:
            obs_id = memory.record_observation(
                db,
                content=content,
                observation_type="goal_completed",
            )
            mem = memory.create_memory(
                db,
                observation_id=obs_id,
                summary=content,
                goal_id=goal_id,
                project_key=project_key,
                tags=["goal_completed", "v0.7.0"],
                confidence=0.9,
            )
            remembered.append({
                "goal_id": goal_id,
                "memory_id": mem.get("id") if isinstance(mem, dict) else mem,
                "project_key": project_key,
            })
        except Exception:
            continue

    return remembered


def sync_goal_statuses(db, limit=100):
    """Run goal aggregator sync for pending/active/blocked goals."""
    db = _require_db(db)
    agg = _load_aggregator()
    results = agg.sync_active_goals(db, limit=limit)
    changed = [r for r in results if r.get("changed")]
    remembered = remember_completed_goals(db, results)
    return {
        "synced": len(results),
        "changed": len(changed),
        "remembered": len(remembered),
        "memories": remembered,
        "results": results,
    }


def run_goal_cycle_once(
    db,
    *,
    max_expand=1,
    template_name="improve_sandbox",
    changes_file="changes.json",
    sync=True,
):
    """One orchestration tick: expand pending → optionally sync statuses."""
    db = _require_db(db)
    expand = expand_pending_goals(
        db,
        max_goals=max_expand,
        template_name=template_name,
        changes_file=changes_file,
    )
    sync_result = None
    if sync:
        sync_result = sync_goal_statuses(db)
    return {
        "expand": expand,
        "sync": sync_result,
    }


def db_connect(path=None):
    db_path = Path(path) if path else DB_FILE
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Goal Cycle v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)

    expand_p = sub.add_parser("expand")
    expand_p.add_argument("--max-goals", type=int, default=1)
    expand_p.add_argument("--template", default="improve_sandbox")
    expand_p.add_argument("--changes-file", default="changes.json")

    sync_p = sub.add_parser("sync")
    sync_p.add_argument("--limit", type=int, default=100)

    cycle_p = sub.add_parser("once")
    cycle_p.add_argument("--max-expand", type=int, default=1)
    cycle_p.add_argument("--template", default="improve_sandbox")
    cycle_p.add_argument("--changes-file", default="changes.json")
    cycle_p.add_argument("--no-sync", action="store_true")

    args = parser.parse_args(argv)
    db = db_connect(args.db)
    try:
        if args.cmd == "expand":
            result = expand_pending_goals(
                db,
                max_goals=args.max_goals,
                template_name=args.template,
                changes_file=args.changes_file,
            )
            print(json.dumps({"ok": True, "result": result}, indent=2))
            return 0
        if args.cmd == "sync":
            result = sync_goal_statuses(db, limit=args.limit)
            print(json.dumps({"ok": True, "result": result}, indent=2))
            return 0
        if args.cmd == "once":
            result = run_goal_cycle_once(
                db,
                max_expand=args.max_expand,
                template_name=args.template,
                changes_file=args.changes_file,
                sync=not args.no_sync,
            )
            print(json.dumps({"ok": True, "result": result}, indent=2))
            return 0
        parser.error(f"unknown command: {args.cmd}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
