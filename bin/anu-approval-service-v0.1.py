#!/usr/bin/env python3
"""ANU Approval Service v0.1 (V0.6.7).

```text
AUTONOMOUS  when safe
APPROVAL    when risky  → WAITING until human decides
DENY        when unsafe
```

CLI channel: request / list / approve / reject / expire.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"

CLASS_AUTONOMOUS = "AUTONOMOUS"
CLASS_APPROVAL = "APPROVAL"
CLASS_DENY = "DENY"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"

DEFAULT_EXPIRY_HOURS = 24

# Hard-deny operations (never approval-gated).
DENY_ACTIONS = frozenset({
    "package",
    "docker",
    "sudo",
    "shell",
    "git_push",
    "deploy",
    "production_deploy",
})

# Risky capabilities that require a recorded approval (when invoked).
APPROVAL_ACTIONS = frozenset({
    "research",
    "github",
    "browser",
    "filesystem.write",
    "download",
    "model",
    "large_file",
    "major_mutation",
    "production_deployment",
})


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _module_path(name):
    for candidate in (
        BASE / "bin" / name,
        _HERE / name,
        _REPO / "bin" / name,
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(name)


def _anu_db():
    path = _module_path("anu-db-v0.1.py")
    spec = importlib.util.spec_from_file_location(
        "anu_db_v0_1_approval",
        path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def db_connect(db_path=None):
    return _anu_db().db_connect(db_path if db_path is not None else DB_FILE)


def _require_db(db):
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")
    return db


def _split_sql(sql: str):
    parts = []
    for chunk in sql.split(";"):
        lines = [
            line
            for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        text = "\n".join(lines).strip()
        if text:
            parts.append(text)
    return parts


def migrate(db):
    """Apply V0.6.7 approvals enrichment (idempotent column adds)."""
    db = _require_db(db)
    # Ensure base table exists (portable tests / fresh DBs).
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            action TEXT NOT NULL,
            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            approved_at TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reason TEXT,
            risk TEXT,
            proposed_change TEXT,
            expires_at TEXT,
            decided_by TEXT,
            decided_at TEXT,
            decision_note TEXT,
            goal_id INTEGER,
            classification TEXT
        )
        """
    )
    sql_path = _REPO / "migrations" / "0009_v0_6_7.sql"
    if not sql_path.is_file():
        sql_path = BASE / "migrations" / "0009_v0_6_7.sql"
    if sql_path.is_file():
        sql = sql_path.read_text(encoding="utf-8")
        for stmt in _split_sql(sql):
            try:
                db.execute(stmt)
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if (
                    "duplicate column" in msg
                    or "already exists" in msg
                ):
                    continue
                raise
    db.commit()


def classify_action(
    *,
    action,
    size_mb=None,
    unknown=False,
    production_impacting=False,
    major_mutation=False,
    capability_disabled=False,
):
    """Map a proposed action to AUTONOMOUS | APPROVAL | DENY."""
    name = str(action or "").strip()
    if unknown or not name:
        return {
            "classification": CLASS_DENY,
            "reason": "unknown_resource",
            "risk": "high",
        }

    if name in DENY_ACTIONS:
        return {
            "classification": CLASS_DENY,
            "reason": f"dangerous_operation:{name}",
            "risk": "critical",
        }

    if production_impacting or name == "production_deployment":
        return {
            "classification": CLASS_APPROVAL,
            "reason": "production_deployment_requires_approval",
            "risk": "high",
        }

    if major_mutation or name == "major_mutation":
        return {
            "classification": CLASS_APPROVAL,
            "reason": "major_project_mutation",
            "risk": "high",
        }

    if name in APPROVAL_ACTIONS or capability_disabled:
        return {
            "classification": CLASS_APPROVAL,
            "reason": f"risky_capability:{name}",
            "risk": "high",
        }

    if size_mb is not None:
        size = float(size_mb)
        if size > 2048:
            return {
                "classification": CLASS_DENY,
                "reason": "file_exceeds_absolute_max",
                "risk": "critical",
            }
        if size > 1024:
            return {
                "classification": CLASS_APPROVAL,
                "reason": "large_file",
                "risk": "medium",
            }
        if size > 250:
            # PREFLIGHT band — autonomous after resource checks (not human gate).
            return {
                "classification": CLASS_AUTONOMOUS,
                "reason": "file_preflight_band",
                "risk": "low",
            }

    return {
        "classification": CLASS_AUTONOMOUS,
        "reason": "safe_default",
        "risk": "low",
    }


def _row_to_record(row):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    return {
        "id": row["id"],
        "task_id": row.get("task_id"),
        "goal_id": row.get("goal_id"),
        "action": row["action"],
        "status": row["status"],
        "reason": row.get("reason"),
        "risk": row.get("risk"),
        "proposed_change": row.get("proposed_change"),
        "classification": row.get("classification"),
        "requested_at": row.get("requested_at"),
        "expires_at": row.get("expires_at"),
        "approved_at": row.get("approved_at"),
        "decided_by": row.get("decided_by"),
        "decided_at": row.get("decided_at"),
        "decision_note": row.get("decision_note"),
    }


def _audit(db, event_type, message, metadata=None, task_id=None, goal_id=None):
    try:
        db.execute(
            """
            INSERT INTO agent_events (task_id, goal_id, event_type, message, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                task_id,
                goal_id,
                event_type,
                message,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
    except sqlite3.OperationalError:
        # Portable DBs may lack agent_events.
        pass


def request_approval(
    db,
    *,
    action,
    task_id=None,
    goal_id=None,
    reason=None,
    risk=None,
    proposed_change=None,
    classification=CLASS_APPROVAL,
    expiry_hours=DEFAULT_EXPIRY_HOURS,
    put_task_waiting=True,
):
    """Create a pending approval record; optionally park the task as waiting."""
    db = _require_db(db)
    migrate(db)

    classified = classify_action(action=action)
    if classified["classification"] == CLASS_DENY:
        raise ApprovalDeniedError(
            classified["reason"]
        )
    if classified["classification"] == CLASS_AUTONOMOUS and classification == CLASS_APPROVAL:
        # Caller forced APPROVAL path (e.g. Governor); keep it.
        pass

    expires = (
        datetime.now(timezone.utc) + timedelta(hours=int(expiry_hours))
    ).isoformat()
    requested = now_iso()
    cur = db.execute(
        """
        INSERT INTO approvals (
            task_id, goal_id, action, requested_at, status,
            reason, risk, proposed_change, expires_at, classification
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            goal_id,
            str(action),
            requested,
            STATUS_PENDING,
            reason or classified.get("reason"),
            risk or classified.get("risk"),
            proposed_change,
            expires,
            classification,
        ),
    )
    approval_id = cur.lastrowid

    if put_task_waiting and task_id is not None:
        _set_task_waiting(db, task_id)
        _register_approval_wake(db, task_id=task_id, goal_id=goal_id, expires=expires)

    _audit(
        db,
        "APPROVAL_REQUESTED",
        f"approval {approval_id} requested for {action}",
        {
            "approval_id": approval_id,
            "action": action,
            "expires_at": expires,
            "risk": risk or classified.get("risk"),
        },
        task_id=task_id,
        goal_id=goal_id,
    )
    db.commit()
    return get_approval(db, approval_id)


def _set_task_waiting(db, task_id):
    try:
        db.execute(
            "UPDATE tasks SET status = 'waiting' WHERE id = ?",
            (task_id,),
        )
    except sqlite3.OperationalError:
        pass


def _register_approval_wake(db, *, task_id, goal_id, expires):
    """V0.6.8: durable approval wake so restart can re-evaluate."""
    try:
        startup = None
        for candidate in (
            BASE / "bin" / "anu-startup-recovery-v0.1.py",
            _HERE / "anu-startup-recovery-v0.1.py",
            _REPO / "bin" / "anu-startup-recovery-v0.1.py",
        ):
            if candidate.is_file():
                spec = importlib.util.spec_from_file_location(
                    "anu_startup_recovery_v0_1_approval",
                    candidate,
                )
                startup = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(startup)
                break
        if startup is None:
            return
        startup.register_wake(
            db,
            wake_kind=startup.WAKE_APPROVAL,
            task_id=task_id,
            goal_id=goal_id,
            wake_at=expires,
            payload={"reason": "approval_pending"},
        )
    except Exception:
        pass


def _set_task_pending(db, task_id):
    cols = [row[1] for row in db.execute("PRAGMA table_info(tasks)")]
    if "retry_after" in cols:
        db.execute(
            """
            UPDATE tasks
            SET status = 'pending', retry_after = NULL, failure_class = NULL
            WHERE id = ?
            """,
            (task_id,),
        )
    else:
        db.execute(
            "UPDATE tasks SET status = 'pending' WHERE id = ?",
            (task_id,),
        )


def _set_task_blocked(db, task_id, failure_class="contract"):
    cols = [row[1] for row in db.execute("PRAGMA table_info(tasks)")]
    if "failure_class" in cols:
        db.execute(
            """
            UPDATE tasks
            SET status = 'blocked', failure_class = ?
            WHERE id = ?
            """,
            (failure_class, task_id),
        )
    else:
        db.execute(
            "UPDATE tasks SET status = 'blocked' WHERE id = ?",
            (task_id,),
        )


def get_approval(db, approval_id):
    db = _require_db(db)
    row = db.execute(
        "SELECT * FROM approvals WHERE id = ?",
        (approval_id,),
    ).fetchone()
    return _row_to_record(row)


def list_pending(db, task_id=None):
    db = _require_db(db)
    expire_pending(db)
    if task_id is None:
        rows = db.execute(
            """
            SELECT * FROM approvals
            WHERE status = ?
            ORDER BY id ASC
            """,
            (STATUS_PENDING,),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT * FROM approvals
            WHERE status = ? AND task_id = ?
            ORDER BY id ASC
            """,
            (STATUS_PENDING, task_id),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def approve(
    db,
    approval_id,
    *,
    decided_by="operator",
    note=None,
    resume_task=True,
):
    db = _require_db(db)
    expire_pending(db)
    record = get_approval(db, approval_id)
    if record is None:
        raise LookupError(f"approval not found: {approval_id}")
    if record["status"] != STATUS_PENDING:
        raise ApprovalStateError(
            f"approval {approval_id} is {record['status']}, not pending"
        )

    decided = now_iso()
    db.execute(
        """
        UPDATE approvals
        SET status = ?, approved_at = ?, decided_by = ?,
            decided_at = ?, decision_note = ?
        WHERE id = ?
        """,
        (
            STATUS_APPROVED,
            decided,
            decided_by,
            decided,
            note,
            approval_id,
        ),
    )
    if resume_task and record.get("task_id") is not None:
        _set_task_pending(db, record["task_id"])
    _audit(
        db,
        "APPROVAL_GRANTED",
        f"approval {approval_id} granted",
        {
            "approval_id": approval_id,
            "decided_by": decided_by,
            "note": note,
        },
        task_id=record.get("task_id"),
        goal_id=record.get("goal_id"),
    )
    db.commit()
    return get_approval(db, approval_id)


def reject(
    db,
    approval_id,
    *,
    decided_by="operator",
    note=None,
):
    db = _require_db(db)
    expire_pending(db)
    record = get_approval(db, approval_id)
    if record is None:
        raise LookupError(f"approval not found: {approval_id}")
    if record["status"] != STATUS_PENDING:
        raise ApprovalStateError(
            f"approval {approval_id} is {record['status']}, not pending"
        )

    decided = now_iso()
    db.execute(
        """
        UPDATE approvals
        SET status = ?, decided_by = ?, decided_at = ?, decision_note = ?
        WHERE id = ?
        """,
        (STATUS_REJECTED, decided_by, decided, note, approval_id),
    )
    if record.get("task_id") is not None:
        _set_task_blocked(db, record["task_id"], failure_class="contract")
    _audit(
        db,
        "APPROVAL_REJECTED",
        f"approval {approval_id} rejected",
        {
            "approval_id": approval_id,
            "decided_by": decided_by,
            "note": note,
        },
        task_id=record.get("task_id"),
        goal_id=record.get("goal_id"),
    )
    db.commit()
    return get_approval(db, approval_id)


def expire_pending(db, now=None):
    """Mark overdue pending approvals expired and block their tasks."""
    db = _require_db(db)
    migrate(db)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    rows = db.execute(
        """
        SELECT id, task_id, goal_id, expires_at FROM approvals
        WHERE status = ?
        """,
        (STATUS_PENDING,),
    ).fetchall()
    expired = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            row = dict(row)
        expires_at = row.get("expires_at")
        if not expires_at:
            continue
        try:
            due = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due > current:
            continue
        decided = now_iso()
        db.execute(
            """
            UPDATE approvals
            SET status = ?, decided_at = ?, decided_by = ?, decision_note = ?
            WHERE id = ?
            """,
            (
                STATUS_EXPIRED,
                decided,
                "system",
                "expired",
                row["id"],
            ),
        )
        if row.get("task_id") is not None:
            _set_task_blocked(db, row["task_id"], failure_class="contract")
        _audit(
            db,
            "APPROVAL_EXPIRED",
            f"approval {row['id']} expired",
            {"approval_id": row["id"]},
            task_id=row.get("task_id"),
            goal_id=row.get("goal_id"),
        )
        expired.append(row["id"])
    if expired:
        db.commit()
    return expired


def has_valid_approval(db, task_id, action):
    """True if an approved, non-expired approval covers this task+action."""
    db = _require_db(db)
    expire_pending(db)
    row = db.execute(
        """
        SELECT * FROM approvals
        WHERE task_id = ? AND action = ? AND status = ?
        ORDER BY id DESC LIMIT 1
        """,
        (task_id, action, STATUS_APPROVED),
    ).fetchone()
    return _row_to_record(row)


def require_autonomous_or_approved(
    db,
    *,
    action,
    task_id=None,
    goal_id=None,
    size_mb=None,
    production_impacting=False,
    major_mutation=False,
    unknown=False,
    proposed_change=None,
):
    """Gate: AUTONOMOUS proceeds; APPROVAL records wait; DENY raises.

    Returns ``{"classification", "approval"}``.
    """
    classified = classify_action(
        action=action,
        size_mb=size_mb,
        unknown=unknown,
        production_impacting=production_impacting,
        major_mutation=major_mutation,
    )
    decision = classified["classification"]
    if decision == CLASS_AUTONOMOUS:
        return {"classification": decision, "approval": None, **classified}
    if decision == CLASS_DENY:
        raise ApprovalDeniedError(classified["reason"])

    if task_id is not None:
        existing = has_valid_approval(db, task_id, action)
        if existing is not None:
            return {
                "classification": CLASS_AUTONOMOUS,
                "approval": existing,
                "reason": "prior_approval",
                "risk": classified.get("risk"),
            }

    approval = request_approval(
        db,
        action=action,
        task_id=task_id,
        goal_id=goal_id,
        reason=classified.get("reason"),
        risk=classified.get("risk"),
        proposed_change=proposed_change,
        classification=CLASS_APPROVAL,
    )
    return {
        "classification": CLASS_APPROVAL,
        "approval": approval,
        **classified,
    }


class ApprovalDeniedError(PermissionError):
    pass


class ApprovalStateError(RuntimeError):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Approval Service v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)

    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--db", default=str(DB_FILE))

    sub.add_parser("migrate", parents=[parent])

    classify_p = sub.add_parser("classify", parents=[parent])
    classify_p.add_argument("--action", required=True)
    classify_p.add_argument("--size-mb", type=float, default=None)
    classify_p.add_argument("--production", action="store_true")
    classify_p.add_argument("--major-mutation", action="store_true")
    classify_p.add_argument("--unknown", action="store_true")

    req = sub.add_parser("request", parents=[parent])
    req.add_argument("--action", required=True)
    req.add_argument("--task-id", type=int, default=None)
    req.add_argument("--goal-id", type=int, default=None)
    req.add_argument("--reason", default=None)
    req.add_argument("--proposed-change", default=None)
    req.add_argument("--expiry-hours", type=int, default=DEFAULT_EXPIRY_HOURS)

    list_p = sub.add_parser("list", parents=[parent])
    list_p.add_argument("--task-id", type=int, default=None)

    ap = sub.add_parser("approve", parents=[parent])
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--by", default="operator")
    ap.add_argument("--note", default=None)

    rj = sub.add_parser("reject", parents=[parent])
    rj.add_argument("--id", type=int, required=True)
    rj.add_argument("--by", default="operator")
    rj.add_argument("--note", default=None)

    sub.add_parser("expire", parents=[parent])

    args = parser.parse_args(argv)
    db_path = getattr(args, "db", str(DB_FILE))
    # Prefer parent --db when duplicated.
    db = db_connect(db_path)
    try:
        if args.cmd == "migrate":
            migrate(db)
            print(json.dumps({"ok": True, "migrated": "0009_v0_6_7"}, indent=2))
            return 0
        if args.cmd == "classify":
            result = classify_action(
                action=args.action,
                size_mb=args.size_mb,
                production_impacting=args.production,
                major_mutation=args.major_mutation,
                unknown=args.unknown,
            )
            print(json.dumps({"ok": True, "result": result}, indent=2))
            return 0
        if args.cmd == "request":
            migrate(db)
            record = request_approval(
                db,
                action=args.action,
                task_id=args.task_id,
                goal_id=args.goal_id,
                reason=args.reason,
                proposed_change=args.proposed_change,
                expiry_hours=args.expiry_hours,
            )
            print(json.dumps({"ok": True, "approval": record}, indent=2))
            return 0
        if args.cmd == "list":
            migrate(db)
            print(json.dumps({
                "ok": True,
                "pending": list_pending(db, task_id=args.task_id),
            }, indent=2))
            return 0
        if args.cmd == "approve":
            migrate(db)
            record = approve(db, args.id, decided_by=args.by, note=args.note)
            print(json.dumps({"ok": True, "approval": record}, indent=2))
            return 0
        if args.cmd == "reject":
            migrate(db)
            record = reject(db, args.id, decided_by=args.by, note=args.note)
            print(json.dumps({"ok": True, "approval": record}, indent=2))
            return 0
        if args.cmd == "expire":
            migrate(db)
            ids = expire_pending(db)
            print(json.dumps({"ok": True, "expired": ids}, indent=2))
            return 0
        parser.error(f"unknown command: {args.cmd}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
