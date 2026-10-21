#!/usr/bin/env python3
"""ANU Autonomy State v0.1 (V0.9.0) — objective, checklist, progress, control.

Persistent goal-level autonomy state in the brain DB. Provider memory and
control flags live here so LLM switches never lose context.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"
WORKSPACE = BASE / "workspace" / "projects"
PROGRESS_FILENAME = "AUTONOMY_PROGRESS.md"
MIGRATION_VERSION = "0011_v0_9_0"

AUTONOMY_EVENTS = frozenset({
    "AUTONOMY_START",
    "AUTONOMY_CONTINUE",
    "AUTONOMY_REPLAN",
    "AUTONOMY_PAUSE",
    "AUTONOMY_STOP",
    "AUTONOMY_COMPLETE",
    "LLM_PROVIDER_SWITCH",
})

CONTROL_STATES = frozenset({"run", "pause", "stop"})
CHECKLIST_STATUSES = frozenset({"pending", "done", "failed", "blocked"})


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _module_path(name):
    for candidate in (BASE / "bin" / name, _HERE / name, _REPO / "bin" / name):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(name)


def _load(name, module_name):
    path = _module_path(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def db_connect(db_path=None):
    anu_db = _load("anu-db-v0.1.py", "anu_db_v090_state")
    return anu_db.db_connect(db_path if db_path is not None else DB_FILE)


def _require_db(db):
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")
    return db


def _migrations_dir():
    for candidate in (
        BASE / "migrations",
        _REPO / "migrations",
        _HERE.parent / "migrations",
    ):
        if candidate.is_dir():
            return candidate
    return _REPO / "migrations"


def migrate(db):
    """Apply V0.9.0 autonomy schema (idempotent, fail-soft on older DBs)."""
    db = _require_db(db)
    migrate_mod = _load("anu-migrate-v0.1.py", "anu_migrate_v090")
    migrate_mod.ensure_schema_migrations_table(db)
    path = _migrations_dir() / f"{MIGRATION_VERSION}.sql"
    if not path.is_file():
        # Inline fallback for packs that only ship the module.
        _apply_inline_migration(db)
        return {"ok": True, "source": "inline"}
    status = migrate_mod.apply_file(db, path)
    return {"ok": True, "source": "file", "status": status}


def _apply_inline_migration(db):
    stmts = [
        "ALTER TABLE goals ADD COLUMN objective TEXT",
        "ALTER TABLE goals ADD COLUMN acceptance_criteria TEXT",
        "ALTER TABLE goals ADD COLUMN autonomous INTEGER NOT NULL DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS goal_checklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            evidence TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS agent_control (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            goal_id INTEGER,
            state TEXT NOT NULL DEFAULT 'run',
            reason TEXT,
            by_who TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope, goal_id)
        )""",
        """CREATE TABLE IF NOT EXISTS autonomy_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            iterations INTEGER NOT NULL DEFAULT 0,
            elapsed_seconds REAL NOT NULL DEFAULT 0,
            stop_reason TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS failure_fingerprints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            task_id INTEGER,
            fingerprint TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            last_message TEXT,
            UNIQUE(goal_id, fingerprint)
        )""",
        """CREATE TABLE IF NOT EXISTS provider_health (
            provider TEXT PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'unknown',
            cooldown_until TEXT,
            last_error TEXT,
            error_class TEXT,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS provider_usage (
            provider TEXT NOT NULL,
            day TEXT NOT NULL,
            requests INTEGER NOT NULL DEFAULT 0,
            tokens INTEGER NOT NULL DEFAULT 0,
            usd REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(provider, day)
        )""",
        """CREATE TABLE IF NOT EXISTS llm_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            task_id INTEGER,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT,
            provider TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS autonomy_checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            snapshot TEXT NOT NULL,
            branch_head TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
        )""",
    ]
    for stmt in stmts:
        try:
            db.execute(stmt)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            raise
    db.commit()


def _column_exists(db, table, column):
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    names = {r[1] if not isinstance(r, sqlite3.Row) else r["name"] for r in rows}
    return column in names


def derive_default_criteria(description: str) -> list[str]:
    """Derive simple acceptance criteria from a goal description."""
    text = (description or "").strip()
    if not text:
        return ["Objective completed with verified checks"]
    return [
        f"Implement: {text}",
        "Tests pass (or compile checks when no tests exist)",
        "Changes confined to the registered project workspace",
    ]


def create_objective(db, goal_id, objective, criteria=None, *, autonomous=True):
    """Attach objective + acceptance criteria to a goal; seed checklist."""
    db = _require_db(db)
    migrate(db)
    goal_id = int(goal_id)
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("objective must be a non-empty string")
    if criteria is None:
        criteria = derive_default_criteria(objective)
    if isinstance(criteria, str):
        criteria = [c.strip() for c in criteria.split(";") if c.strip()]
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("criteria must be a non-empty list")
    criteria = [str(c).strip() for c in criteria if str(c).strip()]
    criteria_json = json.dumps(criteria)
    if not _column_exists(db, "goals", "objective"):
        migrate(db)
    db.execute(
        """UPDATE goals
           SET objective = ?, acceptance_criteria = ?, autonomous = ?
           WHERE id = ?""",
        (objective, criteria_json, 1 if autonomous else 0, goal_id),
    )
    existing = db.execute(
        "SELECT COUNT(*) FROM goal_checklist WHERE goal_id = ?",
        (goal_id,),
    ).fetchone()[0]
    if int(existing) == 0:
        for item in criteria:
            add_checklist_item(db, goal_id, item, commit=False)
    emit_autonomy_event(
        db,
        goal_id,
        "AUTONOMY_START",
        f"objective set for goal {goal_id}",
        {"objective": objective, "criteria": criteria, "autonomous": bool(autonomous)},
        commit=False,
    )
    db.commit()
    return get_objective(db, goal_id)


def get_objective(db, goal_id):
    db = _require_db(db)
    migrate(db)
    row = db.execute(
        """SELECT id, project_id, description, status, objective,
                  acceptance_criteria, autonomous, created_at
           FROM goals WHERE id = ?""",
        (int(goal_id),),
    ).fetchone()
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        data = dict(row)
    else:
        data = {
            "id": row[0],
            "project_id": row[1],
            "description": row[2],
            "status": row[3],
            "objective": row[4],
            "acceptance_criteria": row[5],
            "autonomous": row[6],
            "created_at": row[7],
        }
    raw = data.get("acceptance_criteria")
    if isinstance(raw, str) and raw.strip():
        try:
            data["acceptance_criteria"] = json.loads(raw)
        except json.JSONDecodeError:
            data["acceptance_criteria"] = [raw]
    else:
        data["acceptance_criteria"] = []
    data["autonomous"] = bool(data.get("autonomous"))
    data["checklist"] = list_checklist(db, goal_id)
    return data


def add_checklist_item(db, goal_id, item, *, status="pending", evidence=None, commit=True):
    db = _require_db(db)
    migrate(db)
    if status not in CHECKLIST_STATUSES:
        raise ValueError(f"invalid checklist status: {status}")
    item = (item or "").strip()
    if not item:
        raise ValueError("checklist item must be non-empty")
    cur = db.execute(
        """INSERT INTO goal_checklist(goal_id, item, status, evidence, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (int(goal_id), item, status, evidence, now_iso()),
    )
    if commit:
        db.commit()
    return {
        "id": cur.lastrowid,
        "goal_id": int(goal_id),
        "item": item,
        "status": status,
        "evidence": evidence,
    }


def update_checklist_item(db, item_id, *, status=None, evidence=None, commit=True):
    db = _require_db(db)
    migrate(db)
    row = db.execute(
        "SELECT id, goal_id, item, status, evidence FROM goal_checklist WHERE id = ?",
        (int(item_id),),
    ).fetchone()
    if row is None:
        raise ValueError(f"checklist item not found: {item_id}")
    new_status = status if status is not None else (
        row["status"] if isinstance(row, sqlite3.Row) else row[3]
    )
    if new_status not in CHECKLIST_STATUSES:
        raise ValueError(f"invalid checklist status: {new_status}")
    new_evidence = evidence if evidence is not None else (
        row["evidence"] if isinstance(row, sqlite3.Row) else row[4]
    )
    # Done requires evidence (never claim success without checks).
    if new_status == "done" and not (new_evidence and str(new_evidence).strip()):
        raise ValueError("checklist done requires non-empty evidence")
    db.execute(
        """UPDATE goal_checklist
           SET status = ?, evidence = ?, updated_at = ?
           WHERE id = ?""",
        (new_status, new_evidence, now_iso(), int(item_id)),
    )
    if commit:
        db.commit()
    return list_checklist(db, row["goal_id"] if isinstance(row, sqlite3.Row) else row[1])


def complete_pending_checklist(db, goal_id, evidence, *, commit=True):
    """Mark every pending checklist item done with the same evidence blob.

    Bulk UPDATE so a single bad row cannot leave siblings pending.
    """
    db = _require_db(db)
    migrate(db)
    evidence = (evidence or "").strip()
    if not evidence:
        raise ValueError("checklist done requires non-empty evidence")
    cur = db.execute(
        """UPDATE goal_checklist
           SET status = 'done', evidence = ?, updated_at = ?
           WHERE goal_id = ? AND status = 'pending'""",
        (evidence, now_iso(), int(goal_id)),
    )
    if commit:
        db.commit()
    return {
        "ok": True,
        "updated": int(cur.rowcount or 0),
        "checklist": list_checklist(db, goal_id),
    }


def list_checklist(db, goal_id):
    db = _require_db(db)
    migrate(db)
    rows = db.execute(
        """SELECT id, goal_id, item, status, evidence, updated_at
           FROM goal_checklist WHERE goal_id = ? ORDER BY id""",
        (int(goal_id),),
    ).fetchall()
    out = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            out.append(dict(row))
        else:
            out.append({
                "id": row[0],
                "goal_id": row[1],
                "item": row[2],
                "status": row[3],
                "evidence": row[4],
                "updated_at": row[5],
            })
    return out


def set_control(db, state, *, scope="global", goal_id=None, reason=None, by_who=None):
    db = _require_db(db)
    migrate(db)
    if state not in CONTROL_STATES:
        raise ValueError(f"invalid control state: {state}")
    scope = scope or "global"
    now = now_iso()
    # SQLite UNIQUE treats NULLs as distinct — upsert global rows explicitly.
    if goal_id is None:
        existing = db.execute(
            """SELECT id FROM agent_control
               WHERE scope = ? AND goal_id IS NULL""",
            (scope,),
        ).fetchone()
        if existing is None:
            db.execute(
                """INSERT INTO agent_control(scope, goal_id, state, reason, by_who, updated_at)
                   VALUES (?, NULL, ?, ?, ?, ?)""",
                (scope, state, reason, by_who, now),
            )
        else:
            cid = existing["id"] if isinstance(existing, sqlite3.Row) else existing[0]
            db.execute(
                """UPDATE agent_control
                   SET state = ?, reason = ?, by_who = ?, updated_at = ?
                   WHERE id = ?""",
                (state, reason, by_who, now, cid),
            )
    else:
        db.execute(
            """INSERT INTO agent_control(scope, goal_id, state, reason, by_who, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(scope, goal_id) DO UPDATE SET
                 state=excluded.state,
                 reason=excluded.reason,
                 by_who=excluded.by_who,
                 updated_at=excluded.updated_at""",
            (scope, goal_id, state, reason, by_who, now),
        )
    event = {
        "run": "AUTONOMY_CONTINUE",
        "pause": "AUTONOMY_PAUSE",
        "stop": "AUTONOMY_STOP",
    }[state]
    emit_autonomy_event(
        db,
        goal_id,
        event,
        f"control {scope} -> {state}",
        {"scope": scope, "state": state, "reason": reason, "by": by_who},
        commit=False,
    )
    db.commit()
    return get_control(db, scope=scope, goal_id=goal_id)


def get_control(db, *, scope="global", goal_id=None):
    db = _require_db(db)
    migrate(db)
    if goal_id is not None:
        row = db.execute(
            """SELECT scope, goal_id, state, reason, by_who, updated_at
               FROM agent_control WHERE scope = ? AND goal_id = ?""",
            (scope, int(goal_id)),
        ).fetchone()
        if row is not None:
            return _row_control(row)
    row = db.execute(
        """SELECT scope, goal_id, state, reason, by_who, updated_at
           FROM agent_control WHERE scope = 'global' AND goal_id IS NULL""",
    ).fetchone()
    if row is None:
        return {
            "scope": "global",
            "goal_id": None,
            "state": "run",
            "reason": None,
            "by_who": None,
            "updated_at": None,
        }
    return _row_control(row)


def _row_control(row):
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return {
        "scope": row[0],
        "goal_id": row[1],
        "state": row[2],
        "reason": row[3],
        "by_who": row[4],
        "updated_at": row[5],
    }


def effective_control(db, goal_id=None):
    """Honor stop/pause at global or goal scope (stop wins over pause)."""
    global_ctl = get_control(db, scope="global", goal_id=None)
    goal_ctl = get_control(db, scope="goal", goal_id=goal_id) if goal_id else None
    states = [global_ctl.get("state") or "run"]
    if goal_ctl:
        states.append(goal_ctl.get("state") or "run")
    if "stop" in states:
        winner = goal_ctl if goal_ctl and goal_ctl.get("state") == "stop" else global_ctl
        return {"state": "stop", "control": winner}
    if "pause" in states:
        winner = goal_ctl if goal_ctl and goal_ctl.get("state") == "pause" else global_ctl
        return {"state": "pause", "control": winner}
    return {"state": "run", "control": goal_ctl or global_ctl}


def list_autonomous_goals(db, *, status=None):
    db = _require_db(db)
    migrate(db)
    sql = """SELECT id, project_id, description, status, objective,
                    acceptance_criteria, autonomous, created_at
             FROM goals WHERE autonomous = 1"""
    params = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id"
    rows = db.execute(sql, params).fetchall()
    out = []
    for row in rows:
        gid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        obj = get_objective(db, gid)
        if obj:
            out.append(obj)
    return out


def start_run(db, goal_id):
    db = _require_db(db)
    migrate(db)
    cur = db.execute(
        """INSERT INTO autonomy_runs(goal_id, started_at, status)
           VALUES (?, ?, 'running')""",
        (int(goal_id), now_iso()),
    )
    db.commit()
    return cur.lastrowid


def bump_run_iteration(db, run_id, *, elapsed_seconds=0):
    db = _require_db(db)
    db.execute(
        """UPDATE autonomy_runs
           SET iterations = iterations + 1,
               elapsed_seconds = elapsed_seconds + ?
           WHERE id = ?""",
        (float(elapsed_seconds), int(run_id)),
    )
    db.commit()


def end_run(db, run_id, stop_reason, status="stopped"):
    db = _require_db(db)
    db.execute(
        """UPDATE autonomy_runs
           SET ended_at = ?, stop_reason = ?, status = ?
           WHERE id = ?""",
        (now_iso(), stop_reason, status, int(run_id)),
    )
    db.commit()


def record_failure_fingerprint(db, goal_id, fingerprint, *, task_id=None, message=None):
    db = _require_db(db)
    migrate(db)
    now = now_iso()
    row = db.execute(
        """SELECT id, count FROM failure_fingerprints
           WHERE goal_id = ? AND fingerprint = ?""",
        (int(goal_id), fingerprint),
    ).fetchone()
    if row is None:
        db.execute(
            """INSERT INTO failure_fingerprints
               (goal_id, task_id, fingerprint, count, first_seen, last_seen, last_message)
               VALUES (?, ?, ?, 1, ?, ?, ?)""",
            (int(goal_id), task_id, fingerprint, now, now, message),
        )
        count = 1
    else:
        fid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        prev = row["count"] if isinstance(row, sqlite3.Row) else row[1]
        count = int(prev) + 1
        db.execute(
            """UPDATE failure_fingerprints
               SET count = ?, last_seen = ?, last_message = ?, task_id = COALESCE(?, task_id)
               WHERE id = ?""",
            (count, now, message, task_id, fid),
        )
    db.commit()
    return count


def emit_autonomy_event(db, goal_id, event_type, message, metadata=None, *, commit=True):
    db = _require_db(db)
    if event_type not in AUTONOMY_EVENTS and not str(event_type).startswith("AUTONOMY_"):
        # Allow LLM_PROVIDER_SWITCH and future AUTONOMY_* events.
        if event_type != "LLM_PROVIDER_SWITCH":
            pass
    meta = json.dumps(metadata or {}, default=str)
    try:
        db.execute(
            """INSERT INTO agent_events(event_type, message, metadata, goal_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (event_type, message, meta, goal_id, now_iso()),
        )
        if commit:
            db.commit()
    except sqlite3.Error as exc:
        anu_db = _load("anu-db-v0.1.py", "anu_db_v090_evt")
        anu_db.note_dropped_event(exc, where="autonomy_state")


def render_progress_markdown(db, goal_id, *, current_task=None, blockers=None, elapsed=None):
    obj = get_objective(db, goal_id)
    if obj is None:
        raise ValueError(f"goal not found: {goal_id}")
    checklist = obj.get("checklist") or []
    done = [c for c in checklist if c.get("status") == "done"]
    failed = [c for c in checklist if c.get("status") == "failed"]
    pending = [c for c in checklist if c.get("status") == "pending"]
    blocked = [c for c in checklist if c.get("status") == "blocked"]
    lines = [
        f"# AUTONOMY_PROGRESS — goal {goal_id}",
        "",
        f"Updated: {now_iso()}",
        "",
        "## Objective",
        "",
        obj.get("objective") or obj.get("description") or "(none)",
        "",
        "## Current task",
        "",
        str(current_task) if current_task else "(idle)",
        "",
        "## Done",
        "",
    ]
    if done:
        for c in done:
            lines.append(f"- [x] {c['item']} — evidence: {c.get('evidence') or ''}")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Failed", ""])
    if failed:
        for c in failed:
            lines.append(f"- [!] {c['item']} — {c.get('evidence') or ''}")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Next", ""])
    if pending:
        for c in pending:
            lines.append(f"- [ ] {c['item']}")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Blockers", ""])
    blocker_list = blockers or [c["item"] for c in blocked]
    if blocker_list:
        for b in blocker_list:
            lines.append(f"- {b}")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Elapsed", "", str(elapsed) if elapsed is not None else "(unknown)", ""])
    return "\n".join(lines)


def write_progress_file(db, goal_id, project_key, *, current_task=None, blockers=None, elapsed=None):
    """Atomically write AUTONOMY_PROGRESS.md into the project workspace."""
    text = render_progress_markdown(
        db, goal_id, current_task=current_task, blockers=blockers, elapsed=elapsed
    )
    root = WORKSPACE / project_key
    root.mkdir(parents=True, exist_ok=True)
    target = root / PROGRESS_FILENAME
    fd, tmp_name = tempfile.mkstemp(prefix=".anu-progress-", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return str(target)


def save_checkpoint(db, goal_id, snapshot, *, branch_head=None):
    db = _require_db(db)
    migrate(db)
    payload = snapshot if isinstance(snapshot, str) else json.dumps(snapshot, default=str)
    cur = db.execute(
        """INSERT INTO autonomy_checkpoints(goal_id, snapshot, branch_head, created_at)
           VALUES (?, ?, ?, ?)""",
        (int(goal_id), payload, branch_head, now_iso()),
    )
    db.commit()
    return cur.lastrowid


def latest_checkpoint(db, goal_id):
    db = _require_db(db)
    migrate(db)
    row = db.execute(
        """SELECT id, goal_id, snapshot, branch_head, created_at
           FROM autonomy_checkpoints WHERE goal_id = ?
           ORDER BY id DESC LIMIT 1""",
        (int(goal_id),),
    ).fetchone()
    if row is None:
        return None
    data = dict(row) if isinstance(row, sqlite3.Row) else {
        "id": row[0],
        "goal_id": row[1],
        "snapshot": row[2],
        "branch_head": row[3],
        "created_at": row[4],
    }
    try:
        data["snapshot"] = json.loads(data["snapshot"])
    except (TypeError, json.JSONDecodeError):
        pass
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Autonomy State v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate")
    o = sub.add_parser("set-objective")
    o.add_argument("--goal-id", type=int, required=True)
    o.add_argument("--objective", required=True)
    o.add_argument("--criteria", default=None, help="semicolon-separated")
    o.add_argument("--autonomous", action="store_true")
    g = sub.add_parser("get-objective")
    g.add_argument("--goal-id", type=int, required=True)
    c = sub.add_parser("control")
    c.add_argument("--state", choices=sorted(CONTROL_STATES), required=True)
    c.add_argument("--scope", default="global")
    c.add_argument("--goal-id", type=int, default=None)
    c.add_argument("--reason", default=None)
    c.add_argument("--by", default="cli")
    p = sub.add_parser("progress")
    p.add_argument("--goal-id", type=int, required=True)
    p.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    db = db_connect(args.db)
    try:
        migrate(db)
        if args.cmd == "migrate":
            print(json.dumps({"ok": True}))
        elif args.cmd == "set-objective":
            criteria = None
            if args.criteria:
                criteria = [x.strip() for x in args.criteria.split(";") if x.strip()]
            result = create_objective(
                db, args.goal_id, args.objective, criteria, autonomous=args.autonomous
            )
            print(json.dumps(result, indent=2, default=str))
        elif args.cmd == "get-objective":
            print(json.dumps(get_objective(db, args.goal_id), indent=2, default=str))
        elif args.cmd == "control":
            print(json.dumps(
                set_control(
                    db, args.state, scope=args.scope, goal_id=args.goal_id,
                    reason=args.reason, by_who=args.by,
                ),
                indent=2, default=str,
            ))
        elif args.cmd == "progress":
            path = write_progress_file(db, args.goal_id, args.project)
            print(json.dumps({"ok": True, "path": path}))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
