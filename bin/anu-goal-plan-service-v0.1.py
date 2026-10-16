#!/usr/bin/env python3
"""ANU Goal Plan Service v0.1 (V0.6.1 step 3).

Persists versioned plan artefacts for a goal. Plans are data (serialized
``AgentPlan`` JSON), not executable code. Re-planning creates a new
version and marks the previous active plan ``superseded``.
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
_LIVE_MODEL = BASE / "bin" / "anu-agent-model-v0.1.py"
MODEL_MODULE = (
    _LIVE_MODEL if _LIVE_MODEL.is_file()
    else _HERE / "anu-agent-model-v0.1.py"
)

PLAN_STATUSES = frozenset({
    "active",
    "superseded",
    "abandoned",
})


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


def _validate_goal_id(goal_id):
    if isinstance(goal_id, bool) or not isinstance(goal_id, int):
        raise TypeError("goal_id must be an integer")
    if goal_id < 1:
        raise ValueError("goal_id must be >= 1")
    return goal_id


def _validate_plan_id(plan_id):
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("plan_id must be a non-empty string")
    return plan_id.strip()


def _validate_status(status):
    if not isinstance(status, str):
        raise TypeError("status must be a string")
    if status not in PLAN_STATUSES:
        raise ValueError(
            f"invalid plan status: {status!r}; "
            f"allowed={sorted(PLAN_STATUSES)}"
        )
    return status


def _load_model():
    spec = importlib.util.spec_from_file_location(
        "anu_agent_model_v0_1_plan_service",
        MODEL_MODULE,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {MODEL_MODULE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(db):
    """Create goal_plans storage. Idempotent; does not alter goals."""
    db = _require_db(db)
    if not hasattr(db, "executescript"):
        raise TypeError("db must provide an executescript() method")

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS goal_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            plan_id TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            body_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(goal_id, version),
            FOREIGN KEY(goal_id) REFERENCES goals(id)
        );

        CREATE INDEX IF NOT EXISTS idx_goal_plans_goal_status
            ON goal_plans(goal_id, status);
        """
    )
    db.commit()


def _goal_exists(db, goal_id):
    row = db.execute(
        "SELECT id FROM goals WHERE id = ?",
        (goal_id,),
    ).fetchone()
    return row is not None


def _row_to_record(row):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    return {
        "id": row["id"],
        "goal_id": row["goal_id"],
        "plan_id": row["plan_id"],
        "version": row["version"],
        "status": row["status"],
        "body": json.loads(row["body_json"]),
        "created_at": row["created_at"],
    }


def _coerce_plan_dict(plan):
    """Accept AgentPlan, mapping, or JSON-compatible dict."""
    if hasattr(plan, "to_dict") and callable(plan.to_dict):
        return plan.to_dict()
    if not isinstance(plan, dict):
        raise TypeError("plan must be an AgentPlan or dict")
    return plan


def _validate_plan_body(plan_dict, goal_id):
    """Validate through the frozen AgentPlan contract."""
    model = _load_model()
    plan_dict = dict(plan_dict)

    if "plan_id" not in plan_dict or not plan_dict["plan_id"]:
        plan_dict["plan_id"] = f"plan-{uuid.uuid4().hex[:12]}"

    # AgentPlan.goal_id is a string identity; Brain goals.id is INTEGER.
    expected = str(goal_id)
    incoming = plan_dict.get("goal_id")
    if incoming is None:
        plan_dict["goal_id"] = expected
    else:
        plan_dict["goal_id"] = str(incoming)
        if plan_dict["goal_id"] != expected:
            raise ValueError(
                f"plan goal_id {plan_dict['goal_id']!r} does not match "
                f"goal_id={expected!r}"
            )

    actions = plan_dict.get("actions") or []
    built_actions = []
    for item in actions:
        if hasattr(item, "to_dict") and callable(item.to_dict):
            item = item.to_dict()
        if not isinstance(item, dict):
            raise TypeError("each action must be a dict or AgentAction")
        action = dict(item)
        action["goal_id"] = expected
        built_actions.append(model.AgentAction(**{
            "action_id": action["action_id"],
            "goal_id": action["goal_id"],
            "kind": action["kind"],
            "description": action["description"],
            "parameters": action.get("parameters"),
        }))

    agent_plan = model.AgentPlan(
        plan_id=str(plan_dict["plan_id"]),
        goal_id=expected,
        actions=tuple(built_actions),
    )
    return agent_plan.to_dict()


def save_plan(db, goal_id, plan, status="active"):
    """Persist a new plan version for a goal.

    If ``status`` is ``active``, any current active plan for the goal is
    marked ``superseded`` in the same transaction.
    """
    db = _require_db(db)
    goal_id = _validate_goal_id(goal_id)
    status = _validate_status(status)

    if not _goal_exists(db, goal_id):
        raise ValueError(f"goal does not exist: goal_id={goal_id}")

    body = _validate_plan_body(_coerce_plan_dict(plan), goal_id)
    plan_id = _validate_plan_id(body["plan_id"])

    existing = db.execute(
        "SELECT id FROM goal_plans WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    if existing is not None:
        raise ValueError(f"plan_id already exists: {plan_id}")

    row = db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM goal_plans WHERE goal_id = ?",
        (goal_id,),
    ).fetchone()
    next_version = int(row[0]) + 1

    if status == "active":
        db.execute(
            """UPDATE goal_plans
               SET status = 'superseded'
               WHERE goal_id = ? AND status = 'active'""",
            (goal_id,),
        )

    cur = db.execute(
        """INSERT INTO goal_plans(
               goal_id, plan_id, version, status, body_json, created_at
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            goal_id,
            plan_id,
            next_version,
            status,
            json.dumps(body, sort_keys=True),
            now_iso(),
        ),
    )
    row_id = cur.lastrowid
    db.commit()

    return get_plan_by_row_id(db, row_id)


def get_plan_by_row_id(db, row_id):
    db = _require_db(db)
    if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id < 1:
        raise ValueError("row_id must be a positive integer")
    row = db.execute(
        """SELECT id, goal_id, plan_id, version, status, body_json, created_at
           FROM goal_plans WHERE id = ?""",
        (row_id,),
    ).fetchone()
    return _row_to_record(row)


def get_plan(db, plan_id):
    """Return a plan record by ``plan_id`` string, or None."""
    db = _require_db(db)
    plan_id = _validate_plan_id(plan_id)
    row = db.execute(
        """SELECT id, goal_id, plan_id, version, status, body_json, created_at
           FROM goal_plans WHERE plan_id = ?""",
        (plan_id,),
    ).fetchone()
    return _row_to_record(row)


def get_active_plan(db, goal_id):
    """Return the active plan for a goal, or None."""
    db = _require_db(db)
    goal_id = _validate_goal_id(goal_id)
    row = db.execute(
        """SELECT id, goal_id, plan_id, version, status, body_json, created_at
           FROM goal_plans
           WHERE goal_id = ? AND status = 'active'
           ORDER BY version DESC
           LIMIT 1""",
        (goal_id,),
    ).fetchone()
    return _row_to_record(row)


def list_plans(db, goal_id, limit=50):
    """List plan versions for a goal, newest first."""
    db = _require_db(db)
    goal_id = _validate_goal_id(goal_id)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    rows = db.execute(
        """SELECT id, goal_id, plan_id, version, status, body_json, created_at
           FROM goal_plans
           WHERE goal_id = ?
           ORDER BY version DESC
           LIMIT ?""",
        (goal_id, limit),
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def abandon_active_plan(db, goal_id, reason=None):
    """Mark the active plan abandoned (no replacement)."""
    db = _require_db(db)
    goal_id = _validate_goal_id(goal_id)
    active = get_active_plan(db, goal_id)
    if active is None:
        raise ValueError(f"no active plan for goal_id={goal_id}")

    db.execute(
        """UPDATE goal_plans SET status = 'abandoned' WHERE id = ?""",
        (active["id"],),
    )
    db.commit()
    # reason reserved for events in a later step
    _ = reason
    return get_plan_by_row_id(db, active["id"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Goal Plan Service v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate")

    save_p = sub.add_parser("save")
    save_p.add_argument("--goal-id", type=int, required=True)
    save_p.add_argument(
        "--plan-json",
        required=True,
        help="JSON object with plan_id/actions (goal_id optional)",
    )

    get_p = sub.add_parser("get")
    get_p.add_argument("--plan-id", required=True)

    active_p = sub.add_parser("active")
    active_p.add_argument("--goal-id", type=int, required=True)

    list_p = sub.add_parser("list")
    list_p.add_argument("--goal-id", type=int, required=True)

    args = parser.parse_args(argv)
    db = db_connect(args.db)
    try:
        migrate(db)
        if args.cmd == "migrate":
            print(json.dumps({"ok": True, "migrated": True}))
            return 0
        if args.cmd == "save":
            plan = json.loads(args.plan_json)
            record = save_plan(db, args.goal_id, plan)
        elif args.cmd == "get":
            record = get_plan(db, args.plan_id)
            if record is None:
                print(json.dumps({"ok": False, "error": "not_found"}))
                return 1
        elif args.cmd == "active":
            record = get_active_plan(db, args.goal_id)
            if record is None:
                print(json.dumps({"ok": False, "error": "not_found"}))
                return 1
        elif args.cmd == "list":
            records = list_plans(db, args.goal_id)
            print(json.dumps({"ok": True, "plans": records}, indent=2))
            return 0
        else:
            parser.error(f"unknown command: {args.cmd}")
            return 2

        print(json.dumps({"ok": True, "plan": record}, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
