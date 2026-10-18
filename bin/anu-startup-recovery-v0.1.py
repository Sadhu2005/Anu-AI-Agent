#!/usr/bin/env python3
"""ANU Startup Recovery v0.1 (V0.6.8).

```text
ANU START → load state → find incomplete work → recover → evaluate wakes → continue
```

Composes V0.6.0 mutation recovery with approval expiry, durable wake
evaluation, incomplete-goal inventory, heartbeat, and log rotation.
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
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"
STATE_FILE = BASE / "runtime" / "anu-state.json"
LOG_DIR = BASE / "logs"
EXECUTION_LOCK_FILE = BASE / "runtime" / "anu-execution.lock"

WAKE_TIME = "time"
WAKE_DEPENDENCY = "dependency"
WAKE_APPROVAL = "approval"
WAKE_RESOURCE = "resource"
WAKE_KINDS = frozenset({WAKE_TIME, WAKE_DEPENDENCY, WAKE_APPROVAL, WAKE_RESOURCE})

DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024


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


def _load(name, module_name):
    path = _module_path(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _anu_db():
    return _load("anu-db-v0.1.py", "anu_db_v0_1_startup")


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
    db = _require_db(db)
    sql_path = _REPO / "migrations" / "0010_v0_6_8.sql"
    if not sql_path.is_file():
        sql_path = BASE / "migrations" / "0010_v0_6_8.sql"
    if sql_path.is_file():
        for stmt in _split_sql(sql_path.read_text(encoding="utf-8")):
            try:
                db.execute(stmt)
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "already exists" in msg or "duplicate" in msg:
                    continue
                raise
    else:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS wake_conditions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                goal_id INTEGER,
                wake_kind TEXT NOT NULL,
                wake_at TEXT,
                payload_json TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                satisfied_at TEXT
            )
            """
        )
    db.commit()


def register_wake(
    db,
    *,
    wake_kind,
    task_id=None,
    goal_id=None,
    wake_at=None,
    payload=None,
):
    db = _require_db(db)
    migrate(db)
    if wake_kind not in WAKE_KINDS:
        raise ValueError(f"invalid wake_kind: {wake_kind}")
    cur = db.execute(
        """
        INSERT INTO wake_conditions (
            task_id, goal_id, wake_kind, wake_at, payload_json, status, created_at
        ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            task_id,
            goal_id,
            wake_kind,
            wake_at,
            json.dumps(payload or {}, sort_keys=True),
            now_iso(),
        ),
    )
    db.commit()
    return cur.lastrowid


def _parse_iso(value):
    if value is None:
        return None
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def evaluate_wake_conditions(db, now=None):
    """Satisfy due wakes; return summary of actions taken."""
    db = _require_db(db)
    migrate(db)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    rows = db.execute(
        """
        SELECT * FROM wake_conditions
        WHERE status = 'pending'
        ORDER BY id ASC
        """
    ).fetchall()

    satisfied = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            row = dict(row)
        kind = row["wake_kind"]
        due = False
        if kind == WAKE_TIME:
            wake_at = _parse_iso(row.get("wake_at"))
            due = wake_at is not None and wake_at <= current
        elif kind == WAKE_APPROVAL:
            # Satisfied when no longer a pending approval for this task.
            task_id = row.get("task_id")
            if task_id is None:
                due = True
            else:
                pending = db.execute(
                    """
                    SELECT COUNT(*) FROM approvals
                    WHERE task_id = ? AND status = 'pending'
                    """,
                    (task_id,),
                ).fetchone()[0]
                approved = db.execute(
                    """
                    SELECT COUNT(*) FROM approvals
                    WHERE task_id = ? AND status = 'approved'
                    """,
                    (task_id,),
                ).fetchone()[0]
                due = pending == 0 and approved > 0
        elif kind == WAKE_RESOURCE:
            # Resource wakes clear when retry_after elapsed (task pending).
            wake_at = _parse_iso(row.get("wake_at"))
            due = wake_at is None or wake_at <= current
        elif kind == WAKE_DEPENDENCY:
            # Leave for scheduler graph; mark satisfied if task already pending.
            task_id = row.get("task_id")
            if task_id is not None:
                status = db.execute(
                    "SELECT status FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if status and status[0] == "pending":
                    due = True

        if not due:
            continue

        db.execute(
            """
            UPDATE wake_conditions
            SET status = 'satisfied', satisfied_at = ?
            WHERE id = ?
            """,
            (now_iso(), row["id"]),
        )
        task_id = row.get("task_id")
        if task_id is not None and kind in (WAKE_TIME, WAKE_APPROVAL, WAKE_RESOURCE):
            _resume_task_if_waiting(db, task_id)
        satisfied.append(row["id"])

    if satisfied:
        db.commit()
    return {"satisfied": satisfied, "checked": len(rows)}


def _resume_task_if_waiting(db, task_id):
    row = db.execute(
        "SELECT status FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return
    status = row[0]
    if status != "waiting":
        return
    cols = [c[1] for c in db.execute("PRAGMA table_info(tasks)")]
    if "retry_after" in cols:
        db.execute(
            """
            UPDATE tasks
            SET status = 'pending', retry_after = NULL
            WHERE id = ?
            """,
            (task_id,),
        )
    else:
        db.execute(
            "UPDATE tasks SET status = 'pending' WHERE id = ?",
            (task_id,),
        )


def inventory_incomplete_work(db):
    db = _require_db(db)
    waiting = db.execute(
        "SELECT id, goal_id, status FROM tasks WHERE status = 'waiting'"
    ).fetchall()
    in_flight = db.execute(
        """
        SELECT id, goal_id, status FROM tasks
        WHERE status IN ('planning', 'ready', 'running', 'verifying')
        """
    ).fetchall()
    pending_approvals = []
    try:
        pending_approvals = db.execute(
            "SELECT id, task_id, action FROM approvals WHERE status = 'pending'"
        ).fetchall()
    except sqlite3.OperationalError:
        pass
    active_goals = db.execute(
        """
        SELECT id, status FROM goals
        WHERE status IN ('pending', 'active', 'blocked')
        ORDER BY id ASC
        """
    ).fetchall()

    def _rows(rows):
        out = []
        for row in rows:
            if isinstance(row, sqlite3.Row):
                row = dict(row)
                out.append(row)
            else:
                out.append({"id": row[0], "extra": list(row[1:])})
        return out

    return {
        "waiting_tasks": _rows(waiting),
        "in_flight_tasks": _rows(in_flight),
        "pending_approvals": _rows(pending_approvals),
        "active_goals": _rows(active_goals),
    }


def rotate_logs(log_dir=None, max_bytes=DEFAULT_LOG_MAX_BYTES):
    """Rotate oversized ``*.jsonl`` / ``anu.log`` files in place."""
    root = Path(log_dir) if log_dir else LOG_DIR
    if not root.is_dir():
        return {"rotated": []}
    rotated = []
    for path in sorted(root.glob("*")):
        if not path.is_file():
            continue
        if path.suffix not in (".jsonl", ".log") and path.name != "anu.log":
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < max_bytes:
            continue
        dest = path.with_suffix(path.suffix + ".1")
        if dest.exists():
            dest.unlink()
        path.replace(dest)
        path.write_text("", encoding="utf-8")
        rotated.append({"path": str(path), "archived": str(dest), "bytes": size})
    return {"rotated": rotated}


def write_heartbeat(state_path=None, extra=None):
    path = Path(state_path) if state_path else STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "agent": "ANU",
        "heartbeat_at": now_iso(),
        "status": "running",
    }
    if extra:
        data.update(extra)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return data


def park_for_shutdown(state_path=None, reason="SIGTERM"):
    return write_heartbeat(
        state_path,
        extra={
            "status": "parked",
            "park_reason": reason,
            "parked_at": now_iso(),
        },
    )


def run_startup_sequence(
    db_path=None,
    lock_path=None,
    state_path=None,
    log_dir=None,
    skip_mutation_recovery=False,
):
    """Full V0.6.8 startup: mutate recover → approvals → wakes → inventory."""
    db_path = str(db_path or DB_FILE)
    lock_path = str(lock_path or EXECUTION_LOCK_FILE)
    summary = {
        "ok": True,
        "started_at": now_iso(),
        "mutation_recovery": None,
        "approvals_expired": [],
        "wakes": None,
        "inventory": None,
        "logs": None,
        "heartbeat": None,
    }

    db = db_connect(db_path)
    try:
        migrate(db)

        if not skip_mutation_recovery:
            recovery = _load("anu-recovery-v0.1.py", "anu_recovery_v0_1_startup")
            try:
                summary["mutation_recovery"] = recovery.recover(
                    db_path=db_path,
                    lock_path=lock_path,
                )
            except Exception as exc:
                summary["mutation_recovery"] = {
                    "status": "RECOVERY_ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                }

        try:
            approval = _load(
                "anu-approval-service-v0.1.py",
                "anu_approval_service_v0_1_startup",
            )
            approval.migrate(db)
            summary["approvals_expired"] = approval.expire_pending(db)
        except Exception as exc:
            summary["approvals_expired_error"] = f"{type(exc).__name__}: {exc}"

        summary["wakes"] = evaluate_wake_conditions(db)
        summary["inventory"] = inventory_incomplete_work(db)
        summary["logs"] = rotate_logs(log_dir=log_dir)
        summary["heartbeat"] = write_heartbeat(
            state_path,
            extra={
                "startup_recovery": True,
                "waiting_tasks": len(summary["inventory"]["waiting_tasks"]),
                "active_goals": len(summary["inventory"]["active_goals"]),
            },
        )
        summary["finished_at"] = now_iso()
        return summary
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Startup Recovery v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    parser.add_argument("--lock", default=str(EXECUTION_LOCK_FILE))
    parser.add_argument("--state", default=str(STATE_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate")
    run_p = sub.add_parser("run")
    run_p.add_argument("--skip-mutation-recovery", action="store_true")
    wake_p = sub.add_parser("register-wake")
    wake_p.add_argument("--kind", required=True, choices=sorted(WAKE_KINDS))
    wake_p.add_argument("--task-id", type=int, default=None)
    wake_p.add_argument("--wake-at", default=None)
    sub.add_parser("heartbeat")
    sub.add_parser("park")
    sub.add_parser("rotate-logs")

    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        db = db_connect(args.db)
        try:
            migrate(db)
            print(json.dumps({"ok": True, "migrated": "0010_v0_6_8"}, indent=2))
            return 0
        finally:
            db.close()

    if args.cmd == "run":
        summary = run_startup_sequence(
            db_path=args.db,
            lock_path=args.lock,
            state_path=args.state,
            skip_mutation_recovery=args.skip_mutation_recovery,
        )
        print(json.dumps(summary, indent=2, default=str))
        return 0 if summary.get("ok") else 1

    if args.cmd == "register-wake":
        db = db_connect(args.db)
        try:
            wid = register_wake(
                db,
                wake_kind=args.kind,
                task_id=args.task_id,
                wake_at=args.wake_at,
            )
            print(json.dumps({"ok": True, "wake_id": wid}, indent=2))
            return 0
        finally:
            db.close()

    if args.cmd == "heartbeat":
        print(json.dumps(write_heartbeat(args.state), indent=2))
        return 0

    if args.cmd == "park":
        print(json.dumps(park_for_shutdown(args.state), indent=2))
        return 0

    if args.cmd == "rotate-logs":
        print(json.dumps(rotate_logs(), indent=2))
        return 0

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
