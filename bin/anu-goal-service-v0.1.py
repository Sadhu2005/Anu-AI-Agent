#!/usr/bin/env python3
"""ANU Goal Service v0.1 (V0.6.1 step 2).

CRUD for the existing ``goals`` table. Goals are the top-level unit of work;
plans and task-graph expansion come in later V0.6.1 steps.

This module does not invent schema. It uses the live Brain columns:

    id, project_id, description, status, created_at
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/srv/anu")
DB_FILE = BASE / "brain" / "anu-brain.db"

GOAL_STATUSES = frozenset({
    "pending",
    "active",
    "blocked",
    "completed",
    "failed",
    "cancelled",
})


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db_connect(db_path=None):
    """Open a Brain connection with foreign keys enforced."""
    path = Path(db_path) if db_path is not None else DB_FILE
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _require_db(db):
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")
    return db


def _validate_description(description):
    if not isinstance(description, str):
        raise TypeError("description must be a non-empty string")
    text = description.strip()
    if not text:
        raise ValueError("description must be a non-empty string")
    return text


def _validate_status(status):
    if not isinstance(status, str):
        raise TypeError("status must be a string")
    if status not in GOAL_STATUSES:
        raise ValueError(
            f"invalid goal status: {status!r}; "
            f"allowed={sorted(GOAL_STATUSES)}"
        )
    return status


def _validate_goal_id(goal_id):
    if isinstance(goal_id, bool) or not isinstance(goal_id, int):
        raise TypeError("goal_id must be an integer")
    if goal_id < 1:
        raise ValueError("goal_id must be >= 1")
    return goal_id


def _validate_project_id(project_id):
    if project_id is None:
        return None
    if isinstance(project_id, bool) or not isinstance(project_id, int):
        raise TypeError("project_id must be an integer or None")
    if project_id < 1:
        raise ValueError("project_id must be >= 1")
    return project_id


def _row_to_goal(row):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "description": row["description"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _project_exists(db, project_id):
    row = db.execute(
        "SELECT id FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    return row is not None


def _emit_goal_event(db, goal_id, event_type, message, metadata=None):
    """Best-effort Brain event. Failures must not undo the goal write."""
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


def create_goal(db, description, project_id=None, status="pending"):
    """Insert a goal and return its record."""
    db = _require_db(db)
    description = _validate_description(description)
    status = _validate_status(status)
    project_id = _validate_project_id(project_id)

    if project_id is not None and not _project_exists(db, project_id):
        raise ValueError(f"project does not exist: project_id={project_id}")

    cur = db.execute(
        """INSERT INTO goals(project_id, description, status)
           VALUES (?, ?, ?)""",
        (project_id, description, status),
    )
    goal_id = cur.lastrowid
    db.commit()

    _emit_goal_event(
        db,
        goal_id,
        "GOAL_CREATED",
        f"goal {goal_id}: {status}",
        {
            "goal_id": goal_id,
            "project_id": project_id,
            "status": status,
            "description": description,
        },
    )

    return get_goal(db, goal_id)


def get_goal(db, goal_id):
    """Return a goal dict or None."""
    db = _require_db(db)
    goal_id = _validate_goal_id(goal_id)
    row = db.execute(
        """SELECT id, project_id, description, status, created_at
           FROM goals WHERE id = ?""",
        (goal_id,),
    ).fetchone()
    return _row_to_goal(row)


def list_goals(db, project_id=None, status=None, limit=100):
    """List goals, newest first."""
    db = _require_db(db)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    clauses = []
    params = []

    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(_validate_project_id(project_id))

    if status is not None:
        clauses.append("status = ?")
        params.append(_validate_status(status))

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    rows = db.execute(
        f"""SELECT id, project_id, description, status, created_at
            FROM goals
            {where}
            ORDER BY id DESC
            LIMIT ?""",
        params,
    ).fetchall()
    return [_row_to_goal(row) for row in rows]


def update_goal_status(db, goal_id, status, reason=None):
    """Set goal status. Returns the updated record."""
    db = _require_db(db)
    goal_id = _validate_goal_id(goal_id)
    status = _validate_status(status)

    existing = get_goal(db, goal_id)
    if existing is None:
        raise ValueError(f"goal does not exist: goal_id={goal_id}")

    if reason is not None and not isinstance(reason, str):
        raise TypeError("reason must be a string")

    if existing["status"] == status:
        return existing

    db.execute(
        "UPDATE goals SET status = ? WHERE id = ?",
        (status, goal_id),
    )
    db.commit()

    metadata = {
        "goal_id": goal_id,
        "from_status": existing["status"],
        "to_status": status,
    }
    if reason is not None:
        metadata["reason"] = reason

    _emit_goal_event(
        db,
        goal_id,
        "GOAL_STATUS_CHANGED",
        f"goal {goal_id}: {existing['status']} -> {status}",
        metadata,
    )

    return get_goal(db, goal_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Goal Service v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)

    create_p = sub.add_parser("create")
    create_p.add_argument("--description", required=True)
    create_p.add_argument("--project-id", type=int, default=None)
    create_p.add_argument("--status", default="pending")

    get_p = sub.add_parser("get")
    get_p.add_argument("--goal-id", type=int, required=True)

    list_p = sub.add_parser("list")
    list_p.add_argument("--project-id", type=int, default=None)
    list_p.add_argument("--status", default=None)
    list_p.add_argument("--limit", type=int, default=20)

    status_p = sub.add_parser("set-status")
    status_p.add_argument("--goal-id", type=int, required=True)
    status_p.add_argument("--status", required=True)
    status_p.add_argument("--reason", default=None)

    args = parser.parse_args(argv)
    db = db_connect(args.db)
    try:
        if args.cmd == "create":
            goal = create_goal(
                db,
                args.description,
                project_id=args.project_id,
                status=args.status,
            )
        elif args.cmd == "get":
            goal = get_goal(db, args.goal_id)
            if goal is None:
                print(json.dumps({"ok": False, "error": "not_found"}))
                return 1
        elif args.cmd == "list":
            goals = list_goals(
                db,
                project_id=args.project_id,
                status=args.status,
                limit=args.limit,
            )
            print(json.dumps({"ok": True, "goals": goals}, indent=2))
            return 0
        elif args.cmd == "set-status":
            goal = update_goal_status(
                db,
                args.goal_id,
                args.status,
                reason=args.reason,
            )
        else:
            parser.error(f"unknown command: {args.cmd}")
            return 2

        print(json.dumps({"ok": True, "goal": goal}, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
