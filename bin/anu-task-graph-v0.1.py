#!/usr/bin/env python3
"""ANU Task Graph v0.1

Dependency graph primitives for ANU Task Engine v0.5.3.

Safety model:
- parent_task_id remains hierarchy-only.
- task_dependencies represents execution prerequisites.
- SQLite foreign keys are explicitly enabled for every connection.
- Self-dependencies are rejected.
- Duplicate dependency edges are rejected by UNIQUE constraint.
- Dependency cycles are rejected before insertion.
- Existing task execution/retry/mutation semantics are untouched.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/srv/anu")
DB_FILE = BASE / "brain" / "anu-brain.db"

MAX_ATTEMPTS = 3

WAITING_STATUSES = {
    "pending",
    "ready",
    "planning",
    "running",
}

TERMINAL_SUCCESS = "completed"
TERMINAL_BLOCKED = "blocked"
RETRYABLE_FAILURE = "failed"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db_connect(path=None):
    db = sqlite3.connect(path or DB_FILE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def migrate(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(task_id, depends_on_task_id),

            CHECK(task_id != depends_on_task_id),

            FOREIGN KEY(task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE,

            FOREIGN KEY(depends_on_task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS
        idx_task_dependencies_task
        ON task_dependencies(task_id);

        CREATE INDEX IF NOT EXISTS
        idx_task_dependencies_prerequisite
        ON task_dependencies(depends_on_task_id);
    """)
    db.commit()


def task_exists(db, task_id):
    row = db.execute(
        "SELECT id FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    return row is not None


def would_create_cycle(db, task_id, depends_on_task_id):
    row = db.execute("""
        WITH RECURSIVE reachable(id) AS (
            SELECT depends_on_task_id
            FROM task_dependencies
            WHERE task_id = ?

            UNION

            SELECT td.depends_on_task_id
            FROM task_dependencies td
            JOIN reachable r
              ON td.task_id = r.id
        )
        SELECT 1
        FROM reachable
        WHERE id = ?
        LIMIT 1
    """, (depends_on_task_id, task_id)).fetchone()

    return row is not None


def add_dependency(db, task_id, depends_on_task_id):
    if not task_exists(db, task_id):
        raise ValueError(f"TASK_NOT_FOUND:{task_id}")

    if not task_exists(db, depends_on_task_id):
        raise ValueError(
            f"PREREQUISITE_TASK_NOT_FOUND:{depends_on_task_id}"
        )

    if task_id == depends_on_task_id:
        raise ValueError("SELF_DEPENDENCY")

    if would_create_cycle(db, task_id, depends_on_task_id):
        raise ValueError("DEPENDENCY_CYCLE")

    try:
        db.execute("""
            INSERT INTO task_dependencies(
                task_id,
                depends_on_task_id,
                created_at
            )
            VALUES (?, ?, ?)
        """, (
            task_id,
            depends_on_task_id,
            now_iso(),
        ))
        db.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"DEPENDENCY_INSERT_REJECTED:{exc}"
        ) from exc

    return {
        "task_id": task_id,
        "depends_on_task_id": depends_on_task_id,
    }


def remove_dependency(db, task_id, depends_on_task_id):
    cursor = db.execute("""
        DELETE FROM task_dependencies
        WHERE task_id = ?
          AND depends_on_task_id = ?
    """, (
        task_id,
        depends_on_task_id,
    ))
    db.commit()

    return {
        "task_id": task_id,
        "depends_on_task_id": depends_on_task_id,
        "removed": cursor.rowcount == 1,
    }


def list_dependencies(db, task_id):
    rows = db.execute("""
        SELECT
            td.id,
            td.task_id,
            td.depends_on_task_id,
            t.status AS prerequisite_status,
            t.attempts AS prerequisite_attempts,
            td.created_at
        FROM task_dependencies td
        JOIN tasks t
          ON t.id = td.depends_on_task_id
        WHERE td.task_id = ?
        ORDER BY td.id
    """, (task_id,)).fetchall()

    return [dict(row) for row in rows]


def list_dependents(db, prerequisite_task_id):
    rows = db.execute("""
        SELECT
            td.task_id,
            t.status AS dependent_status,
            t.attempts AS dependent_attempts,
            td.created_at
        FROM task_dependencies td
        JOIN tasks t
          ON t.id = td.task_id
        WHERE td.depends_on_task_id = ?
        ORDER BY td.task_id
    """, (prerequisite_task_id,)).fetchall()

    return [dict(row) for row in rows]


def evaluate_dependents(db, prerequisite_task_id):
    evaluations = []

    for dependent in list_dependents(
        db,
        prerequisite_task_id,
    ):
        state = dependency_readiness(
            db,
            dependent["task_id"],
        )

        evaluations.append({
            "task_id": dependent["task_id"],
            "current_status": dependent["dependent_status"],
            "current_attempts": dependent["dependent_attempts"],
            "state": state["state"],
            "reason": state["reason"],
            "dependency_state": state,
        })

    return evaluations


def dependency_readiness(db, task_id):
    if not task_exists(db, task_id):
        return {
            "task_id": task_id,
            "state": "INVALID",
            "reason": "TASK_NOT_FOUND",
            "dependencies": [],
        }

    dependencies = list_dependencies(db, task_id)

    if not dependencies:
        return {
            "task_id": task_id,
            "state": "READY",
            "reason": "NO_DEPENDENCIES",
            "dependencies": [],
        }

    blocked = []
    waiting = []

    for dependency in dependencies:
        status = dependency["prerequisite_status"]
        attempts = dependency["prerequisite_attempts"]

        if status == TERMINAL_SUCCESS:
            continue

        if status == TERMINAL_BLOCKED:
            blocked.append({
                "task_id": dependency["depends_on_task_id"],
                "reason": "PREREQUISITE_BLOCKED",
            })
            continue

        if status == RETRYABLE_FAILURE:
            if attempts >= MAX_ATTEMPTS:
                blocked.append({
                    "task_id": dependency["depends_on_task_id"],
                    "reason": "PREREQUISITE_RETRY_EXHAUSTED",
                })
            else:
                waiting.append({
                    "task_id": dependency["depends_on_task_id"],
                    "reason": "PREREQUISITE_RETRYABLE_FAILURE",
                })
            continue

        if status in WAITING_STATUSES:
            waiting.append({
                "task_id": dependency["depends_on_task_id"],
                "reason": f"PREREQUISITE_{status.upper()}",
            })
            continue

        blocked.append({
            "task_id": dependency["depends_on_task_id"],
            "reason": f"UNKNOWN_PREREQUISITE_STATUS:{status}",
        })

    if blocked:
        state = "BLOCKED"
        reason = "PREREQUISITE_TERMINALLY_UNAVAILABLE"
    elif waiting:
        state = "WAITING"
        reason = "PREREQUISITES_INCOMPLETE"
    else:
        state = "READY"
        reason = "ALL_DEPENDENCIES_COMPLETED"

    return {
        "task_id": task_id,
        "state": state,
        "reason": reason,
        "dependencies": dependencies,
        "blocked": blocked,
        "waiting": waiting,
    }


def cmd_migrate(args):
    db = db_connect(args.database)
    try:
        migrate(db)
        print(json.dumps({
            "ok": True,
            "operation": "migrate",
            "foreign_keys": db.execute(
                "PRAGMA foreign_keys"
            ).fetchone()[0],
        }, indent=2))
    finally:
        db.close()


def cmd_add(args):
    db = db_connect(args.database)
    try:
        migrate(db)
        result = add_dependency(
            db,
            args.task_id,
            args.depends_on,
        )
        print(json.dumps({
            "ok": True,
            "operation": "add_dependency",
            **result,
        }, indent=2))
    except ValueError as exc:
        print(json.dumps({
            "ok": False,
            "error": str(exc),
        }, indent=2))
        return 1
    finally:
        db.close()


def cmd_remove(args):
    db = db_connect(args.database)
    try:
        migrate(db)
        result = remove_dependency(
            db,
            args.task_id,
            args.depends_on,
        )
        print(json.dumps({
            "ok": True,
            "operation": "remove_dependency",
            **result,
        }, indent=2))
    finally:
        db.close()


def cmd_list(args):
    db = db_connect(args.database)
    try:
        migrate(db)
        print(json.dumps({
            "ok": True,
            "operation": "list_dependencies",
            "task_id": args.task_id,
            "dependencies": list_dependencies(
                db,
                args.task_id,
            ),
        }, indent=2))
    finally:
        db.close()


def cmd_ready(args):
    db = db_connect(args.database)
    try:
        migrate(db)
        print(json.dumps(
            dependency_readiness(db, args.task_id),
            indent=2,
        ))
    finally:
        db.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description="ANU Task Graph v0.1"
    )
    parser.add_argument(
        "--database",
        default=str(DB_FILE),
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    migrate_parser = sub.add_parser("migrate")
    migrate_parser.set_defaults(func=cmd_migrate)

    add_parser = sub.add_parser("add")
    add_parser.add_argument(
        "--task-id",
        type=int,
        required=True,
    )
    add_parser.add_argument(
        "--depends-on",
        type=int,
        required=True,
    )
    add_parser.set_defaults(func=cmd_add)

    remove_parser = sub.add_parser("remove")
    remove_parser.add_argument(
        "--task-id",
        type=int,
        required=True,
    )
    remove_parser.add_argument(
        "--depends-on",
        type=int,
        required=True,
    )
    remove_parser.set_defaults(func=cmd_remove)

    list_parser = sub.add_parser("list")
    list_parser.add_argument(
        "--task-id",
        type=int,
        required=True,
    )
    list_parser.set_defaults(func=cmd_list)

    ready_parser = sub.add_parser("ready")
    ready_parser.add_argument(
        "--task-id",
        type=int,
        required=True,
    )
    ready_parser.set_defaults(func=cmd_ready)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
