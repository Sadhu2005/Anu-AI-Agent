#!/usr/bin/env python3
"""ANU Goal Service v0.1 (V0.6.1 step 2).

CRUD for the existing ``goals`` table. Goals are the top-level unit of work;
plans and task-graph expansion come in later V0.6.1 steps.

This module does not invent schema. It uses the live Brain columns:

    id, project_id, description, status, created_at
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DB_FILE = BASE / "brain" / "anu-brain.db"
_HERE = Path(__file__).resolve().parent

GOAL_STATUSES = frozenset({
    "pending",
    "active",
    "blocked",
    "completed",
    "failed",
    "cancelled",
})

# F15: allowed goal status transitions (same status always ok).
GOAL_TRANSITIONS = {
    "pending": frozenset({"active", "blocked", "completed", "failed", "cancelled"}),
    "active": frozenset({"blocked", "completed", "failed", "cancelled"}),
    "blocked": frozenset({"active", "completed", "failed", "cancelled"}),
    "failed": frozenset({"active", "blocked", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _anu_db():
    for candidate in (
        BASE / "bin" / "anu-db-v0.1.py",
        _HERE / "anu-db-v0.1.py",
    ):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "anu_db_v0_1_goal_service",
                candidate,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError("anu-db-v0.1.py not found")


def db_connect(db_path=None):
    """Open a Brain connection with foreign keys enforced."""
    return _anu_db().db_connect(db_path if db_path is not None else DB_FILE)


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


def _emit_goal_event(
    db,
    goal_id,
    event_type,
    message,
    metadata=None,
    *,
    commit=True,
):
    """Insert a Brain event. When ``commit`` is False, caller owns the txn."""
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
        if commit:
            db.commit()
    except Exception as exc:
        if commit:
            try:
                db.rollback()
            except Exception:
                pass
        try:
            _anu_db().note_dropped_event(exc, where="goal_service.event")
        except Exception:
            print(
                f"ANU_DROPPED_EVENT where=goal_service.event: {exc}",
                file=sys.stderr,
                flush=True,
            )
        if not commit:
            raise


def _assert_transition(from_status, to_status):
    if from_status == to_status:
        return
    allowed = GOAL_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise ValueError(
            f"invalid goal transition: {from_status!r} -> {to_status!r}; "
            f"allowed={sorted(allowed)}"
        )


def create_goal(db, description, project_id=None, status="pending"):
    """Insert a goal and return its record."""
    db = _require_db(db)
    description = _validate_description(description)
    status = _validate_status(status)
    project_id = _validate_project_id(project_id)

    if project_id is not None and not _project_exists(db, project_id):
        raise ValueError(f"project does not exist: project_id={project_id}")

    try:
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute(
            """INSERT INTO goals(project_id, description, status)
               VALUES (?, ?, ?)""",
            (project_id, description, status),
        )
        goal_id = cur.lastrowid
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
            commit=False,
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise

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


def purge_goal(db, goal_id, *, by_who="operator"):
    """Permanently remove a goal and its brain work/memory (not the git repo).

    Deletes tasks, events, memories, checklist, checkpoints, control rows, etc.
    Shared project workspace files and remote ``agent/*`` branches are left
    intact (lab repos are shared across goals) — operator merges/deletes those
    on GitHub if desired.
    """
    db = _require_db(db)
    goal_id = _validate_goal_id(goal_id)
    existing = get_goal(db, goal_id)
    if existing is None:
        return {"ok": False, "error": "not_found", "goal_id": goal_id}

    try:
        db.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error:
        pass

    task_ids = [
        (r[0] if not isinstance(r, sqlite3.Row) else r["id"])
        for r in db.execute(
            "SELECT id FROM tasks WHERE goal_id = ?",
            (goal_id,),
        ).fetchall()
    ]

    deleted = {"goal_id": goal_id, "tasks": len(task_ids), "tables": {}}
    errors = []

    def _run(label, sql, params=()):
        try:
            cur = db.execute(sql, params)
            deleted["tables"][label] = cur.rowcount if cur.rowcount is not None else 0
        except sqlite3.Error as exc:
            deleted["tables"][label] = f"skip:{exc}"
            errors.append(f"{label}: {exc}")

    # Goal-scoped rows that do not depend on task deletion order.
    for label, sql in (
        ("wake_conditions_goal", "DELETE FROM wake_conditions WHERE goal_id = ?"),
        ("agent_events", "DELETE FROM agent_events WHERE goal_id = ?"),
        ("approvals_goal", "DELETE FROM approvals WHERE goal_id = ?"),
        ("autonomy_checkpoints", "DELETE FROM autonomy_checkpoints WHERE goal_id = ?"),
        ("autonomy_runs", "DELETE FROM autonomy_runs WHERE goal_id = ?"),
        ("failure_fingerprints", "DELETE FROM failure_fingerprints WHERE goal_id = ?"),
        ("goal_checklist", "DELETE FROM goal_checklist WHERE goal_id = ?"),
        ("goal_plans", "DELETE FROM goal_plans WHERE goal_id = ?"),
        ("llm_context", "DELETE FROM llm_context WHERE goal_id = ?"),
        ("agent_control", "DELETE FROM agent_control WHERE goal_id = ?"),
    ):
        _run(label, sql, (goal_id,))

    # Task-scoped children (memories before observations — FK).
    if task_ids:
        placeholders = ",".join("?" * len(task_ids))
        tids = tuple(task_ids)
        _run(
            "agent_memories_task",
            f"DELETE FROM agent_memories WHERE task_id IN ({placeholders})",
            tids,
        )
        _run(
            "agent_memories_goal",
            "DELETE FROM agent_memories WHERE goal_id = ?",
            (goal_id,),
        )
        _run(
            "task_dependencies",
            f"DELETE FROM task_dependencies WHERE task_id IN ({placeholders}) "
            f"OR depends_on_task_id IN ({placeholders})",
            tids + tids,
        )
        for label, col_sql in (
            ("task_execution_specs", f"DELETE FROM task_execution_specs WHERE task_id IN ({placeholders})"),
            ("verification_records", f"DELETE FROM verification_records WHERE task_id IN ({placeholders})"),
            ("test_results", f"DELETE FROM test_results WHERE task_id IN ({placeholders})"),
            ("actions", f"DELETE FROM actions WHERE task_id IN ({placeholders})"),
            ("errors", f"DELETE FROM errors WHERE task_id IN ({placeholders})"),
            ("research", f"DELETE FROM research WHERE task_id IN ({placeholders})"),
            ("commits", f"DELETE FROM commits WHERE task_id IN ({placeholders})"),
            ("checkpoints", f"DELETE FROM checkpoints WHERE task_id IN ({placeholders})"),
            ("mutation_transactions", f"DELETE FROM mutation_transactions WHERE task_id IN ({placeholders})"),
            ("approvals_task", f"DELETE FROM approvals WHERE task_id IN ({placeholders})"),
            ("wake_conditions_task", f"DELETE FROM wake_conditions WHERE task_id IN ({placeholders})"),
            ("observations", f"DELETE FROM observations WHERE task_id IN ({placeholders})"),
        ):
            _run(label, col_sql, tids)
        # Break self-FK on parent_task_id before deleting tasks.
        _run(
            "clear_parent_task",
            f"UPDATE tasks SET parent_task_id = NULL WHERE goal_id = ? "
            f"OR parent_task_id IN ({placeholders})",
            (goal_id,) + tids,
        )
    else:
        _run(
            "agent_memories_goal",
            "DELETE FROM agent_memories WHERE goal_id = ?",
            (goal_id,),
        )

    _run("tasks", "DELETE FROM tasks WHERE goal_id = ?", (goal_id,))
    _run("goals", "DELETE FROM goals WHERE id = ?", (goal_id,))

    still = get_goal(db, goal_id)
    if still is not None:
        try:
            db.rollback()
        except sqlite3.Error:
            pass
        return {
            "ok": False,
            "deleted": False,
            "error": "goal_row_still_present",
            "goal_id": goal_id,
            "fk_errors": errors,
            "counts": deleted,
        }

    try:
        db.commit()
    except sqlite3.Error as exc:
        try:
            db.rollback()
        except sqlite3.Error:
            pass
        return {"ok": False, "error": str(exc), "goal_id": goal_id, "partial": deleted}

    return {
        "ok": True,
        "deleted": True,
        "goal_id": goal_id,
        "by": by_who,
        "was": existing,
        "counts": deleted,
        "note": "Brain work/memory removed. Shared workspace files and GitHub agent/* branches were not deleted.",
    }


def update_goal_status(db, goal_id, status, reason=None, force=False):
    """Set goal status. Returns the updated record.

    Goal row update and GOAL_STATUS_CHANGED event share one transaction (F8).
    ``force=True`` bypasses transition rules (aggregator cancel override).
    """
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

    if not force:
        _assert_transition(existing["status"], status)

    metadata = {
        "goal_id": goal_id,
        "from_status": existing["status"],
        "to_status": status,
    }
    if reason is not None:
        metadata["reason"] = reason

    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE goals SET status = ? WHERE id = ?",
            (status, goal_id),
        )
        _emit_goal_event(
            db,
            goal_id,
            "GOAL_STATUS_CHANGED",
            f"goal {goal_id}: {existing['status']} -> {status}",
            metadata,
            commit=False,
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise

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
