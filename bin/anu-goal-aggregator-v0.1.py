#!/usr/bin/env python3
"""ANU Goal Aggregator v0.1 (V0.6.1 step 5).

Derive goal status from the task graph belonging to that goal.

Rules (deterministic):
- no tasks → no change (keep current goal status)
- every task ``completed`` → ``completed``
- any task ``blocked`` → ``blocked``
- otherwise any non-terminal work (pending/ready/planning/running/
  verifying/failed) → ``active``  (``failed`` remains retryable)

Cancelled goals are left alone unless ``force=True``.
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

TERMINAL_SUCCESS = "completed"
TERMINAL_BLOCKED = "blocked"
IGNORED_TASK_STATUSES = frozenset({
    "superseded",
    "cancelled",
})
ACTIVE_TASK_STATUSES = frozenset({
    "pending",
    "ready",
    "planning",
    "running",
    "verifying",
    "failed",
})


def db_connect(db_path=None):
    for candidate in (
        BASE / "bin" / "anu-db-v0.1.py",
        _HERE / "anu-db-v0.1.py",
    ):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "anu_db_v0_1_aggregator",
                candidate,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.db_connect(
                db_path if db_path is not None else DB_FILE
            )
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


def _load_goal_service():
    path = _module_path("anu-goal-service-v0.1.py")
    spec = importlib.util.spec_from_file_location(
        "anu_goal_service_v0_1_aggregator",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def list_goal_task_statuses(db, goal_id):
    """Return ``[{id, status, attempts}, ...]`` for a goal."""
    db = _require_db(db)
    if isinstance(goal_id, bool) or not isinstance(goal_id, int) or goal_id < 1:
        raise ValueError("goal_id must be a positive integer")

    rows = db.execute(
        """SELECT id, status, attempts
           FROM tasks
           WHERE goal_id = ?
           ORDER BY id ASC""",
        (goal_id,),
    ).fetchall()

    out = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            row = dict(row)
        out.append({
            "id": row["id"],
            "status": row["status"],
            "attempts": row["attempts"],
        })
    return out


def derive_goal_status(task_statuses):
    """Derive a goal status string from task status values.

    ``task_statuses`` may be a list of status strings or task dicts with
    a ``status`` key. Returns ``None`` when there are no tasks (caller
    should leave the goal unchanged).
    """
    if task_statuses is None:
        raise TypeError("task_statuses must be a list")

    statuses = []
    for item in task_statuses:
        if isinstance(item, str):
            status = item
        elif isinstance(item, dict):
            status = item.get("status")
            if not isinstance(status, str):
                raise TypeError("task status must be a string")
        else:
            raise TypeError(
                "task_statuses items must be strings or dicts"
            )
        if status in IGNORED_TASK_STATUSES:
            continue
        statuses.append(status)

    if not statuses:
        return None

    if any(s == TERMINAL_BLOCKED for s in statuses):
        return "blocked"

    if all(s == TERMINAL_SUCCESS for s in statuses):
        return "completed"

    if any(s in ACTIVE_TASK_STATUSES for s in statuses):
        return "active"

    # Unexpected mix (e.g. only unknown labels): stay conservative.
    return "active"


def _load_verification():
    for candidate in (
        BASE / "bin" / "anu-verification-v0.1.py",
        _HERE / "anu-verification-v0.1.py",
    ):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "anu_verification_v0_1_aggregator",
                candidate,
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def sync_goal_status(db, goal_id, force=False):
    """Recompute goal status from its tasks and persist if it changes.

    Returns a summary dict including ``derived``, ``changed``, and
    ``task_summary``.

    V0.6.5: a goal only becomes ``completed`` when every task has a
    latest ACCEPT verification record (goal-level acceptance).
    """
    db = _require_db(db)
    goals = _load_goal_service()
    goal = goals.get_goal(db, goal_id)
    if goal is None:
        raise ValueError(f"goal does not exist: goal_id={goal_id}")

    tasks = list_goal_task_statuses(db, goal_id)
    derived = derive_goal_status(tasks)
    verification_record = None

    if derived == TERMINAL_SUCCESS:
        verification = _load_verification()
        if verification is not None:
            try:
                verification.migrate(db)
                verification_record = verification.evaluate_goal_acceptance(
                    db, goal_id, persist=True
                )
                if not verification_record.get("goal_ok"):
                    derived = "active"
            except Exception:
                derived = "active"

    summary = {
        "goal_id": goal_id,
        "before": goal["status"],
        "derived": derived,
        "changed": False,
        "task_count": len(tasks),
        "task_summary": _count_statuses(tasks),
        "goal": goal,
        "verification": verification_record,
    }

    if derived is None:
        summary["reason"] = "no_tasks"
        return summary

    if goal["status"] == "cancelled" and not force:
        summary["reason"] = "goal_cancelled"
        return summary

    if goal["status"] == derived:
        summary["reason"] = "unchanged"
        return summary

    reason = f"derived from {len(tasks)} tasks"
    if (
        verification_record is not None
        and derived == TERMINAL_SUCCESS
        and verification_record.get("goal_ok")
    ):
        reason = f"goal acceptance OK ({len(tasks)} tasks)"
    elif verification_record is not None and derived == "active":
        reason = "goal acceptance pending verification"

    updated = goals.update_goal_status(
        db,
        goal_id,
        derived,
        reason=reason,
        force=force,
    )
    summary["changed"] = True
    summary["after"] = updated["status"]
    summary["goal"] = updated
    summary["reason"] = "updated"
    return summary


def sync_active_goals(db, limit=100):
    """Sync all goals currently marked active (or pending with tasks)."""
    db = _require_db(db)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    rows = db.execute(
        """SELECT id FROM goals
           WHERE status IN ('pending', 'active', 'blocked')
           ORDER BY id ASC
           LIMIT ?""",
        (limit,),
    ).fetchall()

    results = []
    for row in rows:
        goal_id = row[0] if not isinstance(row, sqlite3.Row) else row["id"]
        results.append(sync_goal_status(db, goal_id))
    return results


def _count_statuses(tasks):
    counts = {}
    for task in tasks:
        status = task["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Goal Aggregator v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sync_p = sub.add_parser("sync")
    sync_p.add_argument("--goal-id", type=int, required=True)
    sync_p.add_argument("--force", action="store_true")

    sub.add_parser("sync-active")

    derive_p = sub.add_parser("derive")
    derive_p.add_argument(
        "--statuses",
        required=True,
        help="comma-separated task statuses",
    )

    args = parser.parse_args(argv)

    if args.cmd == "derive":
        statuses = [s.strip() for s in args.statuses.split(",") if s.strip()]
        print(json.dumps({
            "ok": True,
            "derived": derive_goal_status(statuses),
        }, indent=2))
        return 0

    db = db_connect(args.db)
    try:
        if args.cmd == "sync":
            result = sync_goal_status(db, args.goal_id, force=args.force)
            print(json.dumps({"ok": True, "result": result}, indent=2))
            return 0
        if args.cmd == "sync-active":
            results = sync_active_goals(db)
            print(json.dumps({"ok": True, "results": results}, indent=2))
            return 0
        parser.error(f"unknown command: {args.cmd}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
