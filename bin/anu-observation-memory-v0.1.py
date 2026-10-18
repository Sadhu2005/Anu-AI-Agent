#!/usr/bin/env python3
"""ANU Observation / Memory service v0.1 (V0.6.3).

```text
capability result → observation → interpreted memory → retrieval
```

Uses existing ``observations`` for raw captures and additive
``agent_memories`` for provenance, confidence, and expiry.
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

DEFAULT_TTL_HOURS = 72
DEFAULT_MAX_MEMORIES_PER_PROJECT = 50
OBS_TYPE_CAPABILITY = "capability_result"

# Map coarse outcome → failure_class tags (V0.6.0 vocabulary where useful).
FAILURE_CLASS_BY_SIGNAL = {
    "timeout": "environment",
    "denied": "contract",
    "capability_denied": "contract",
    "test_failure": "code_defect",
    "integrity": "integrity",
}


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
        "anu_db_v0_1_obs_mem",
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


def migrate(db):
    """Apply V0.6.3 agent_memories DDL (idempotent)."""
    db = _require_db(db)
    sql_path = _REPO / "migrations" / "0006_v0_6_3.sql"
    if not sql_path.is_file():
        sql_path = BASE / "migrations" / "0006_v0_6_3.sql"
    sql = sql_path.read_text(encoding="utf-8")
    for stmt in _split_sql(sql):
        try:
            db.execute(stmt)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "duplicate" in msg:
                continue
            raise
    db.commit()


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


def _parse_iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def classify_capability_result(
    *,
    capability,
    ok,
    exit_code=None,
    denied=False,
    denial_class=None,
    error=None,
):
    """Return (observation_summary, failure_class, tags)."""
    tags = []
    if capability:
        tags.append(f"capability:{capability}")
    if denied:
        tags.append("denied")
        fc = denial_class or "contract"
        if denial_class:
            tags.append(f"denial:{denial_class}")
        summary = f"capability denied: {capability} ({fc})"
        return summary, FAILURE_CLASS_BY_SIGNAL.get(fc, fc), tags

    if ok:
        tags.append("success")
        summary = f"capability ok: {capability}"
        return summary, None, tags

    tags.append("failure")
    err = (error or "").lower()
    failure_class = "environment"
    if "timeout" in err:
        failure_class = "environment"
        tags.append("timeout")
    elif "integrity" in err or "rollback" in err:
        failure_class = "integrity"
    elif exit_code not in (None, 0):
        failure_class = "code_defect"
        tags.append(f"exit:{exit_code}")
    summary = f"capability failed: {capability} ({failure_class})"
    return summary, failure_class, tags


def record_observation(
    db,
    *,
    content,
    observation_type=OBS_TYPE_CAPABILITY,
    task_id=None,
):
    db = _require_db(db)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a non-empty string")
    if not isinstance(observation_type, str) or not observation_type.strip():
        raise ValueError("observation_type must be a non-empty string")
    if task_id is not None and (
        isinstance(task_id, bool) or not isinstance(task_id, int) or task_id < 1
    ):
        raise ValueError("task_id must be a positive integer or None")

    cur = db.execute(
        """INSERT INTO observations(task_id, observation_type, content, created_at)
           VALUES (?, ?, ?, ?)""",
        (task_id, observation_type.strip(), content, now_iso()),
    )
    db.commit()
    return int(cur.lastrowid)


def create_memory(
    db,
    *,
    observation_id,
    summary,
    task_id=None,
    goal_id=None,
    project_key=None,
    capability=None,
    failure_class=None,
    tags=None,
    confidence=0.5,
    ttl_hours=DEFAULT_TTL_HOURS,
    created_at=None,
):
    db = _require_db(db)
    if isinstance(observation_id, bool) or not isinstance(observation_id, int):
        raise TypeError("observation_id must be an integer")
    if observation_id < 1:
        raise ValueError("observation_id must be >= 1")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must be a non-empty string")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise TypeError("confidence must be a number")
    confidence = float(confidence)
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError("confidence must be between 0 and 1")

    created = created_at or now_iso()
    expires_at = None
    if ttl_hours is not None:
        if isinstance(ttl_hours, bool) or not isinstance(ttl_hours, (int, float)):
            raise TypeError("ttl_hours must be a number or None")
        if float(ttl_hours) < 0:
            raise ValueError("ttl_hours must be >= 0")
        expires_at = (
            _parse_iso(created) + timedelta(hours=float(ttl_hours))
        ).isoformat()

    tags_json = json.dumps(list(tags or []), sort_keys=True)
    cur = db.execute(
        """INSERT INTO agent_memories(
               observation_id, task_id, goal_id, project_key, capability,
               failure_class, tags_json, summary, confidence, expires_at,
               created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            observation_id,
            task_id,
            goal_id,
            project_key,
            capability,
            failure_class,
            tags_json,
            summary.strip(),
            confidence,
            expires_at,
            created,
        ),
    )
    db.commit()
    return get_memory(db, int(cur.lastrowid))


def get_memory(db, memory_id):
    db = _require_db(db)
    row = db.execute(
        """SELECT id, observation_id, task_id, goal_id, project_key, capability,
                  failure_class, tags_json, summary, confidence, expires_at,
                  created_at
           FROM agent_memories WHERE id = ?""",
        (memory_id,),
    ).fetchone()
    return _row_to_memory(row)


def _row_to_memory(row):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    tags = []
    raw_tags = row.get("tags_json") or "[]"
    try:
        parsed = json.loads(raw_tags)
        if isinstance(parsed, list):
            tags = parsed
    except json.JSONDecodeError:
        tags = []
    return {
        "id": row["id"],
        "observation_id": row["observation_id"],
        "task_id": row["task_id"],
        "goal_id": row["goal_id"],
        "project_key": row["project_key"],
        "capability": row["capability"],
        "failure_class": row["failure_class"],
        "tags": tags,
        "summary": row["summary"],
        "confidence": row["confidence"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
    }


def is_expired(memory, now=None):
    if memory is None:
        return True
    expires = memory.get("expires_at")
    if not expires:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        return _parse_iso(expires) <= now
    except ValueError:
        return False


def record_capability_result(
    db,
    *,
    project_key,
    capability,
    ok,
    task_id=None,
    goal_id=None,
    exit_code=None,
    denied=False,
    denial_class=None,
    error=None,
    detail=None,
    confidence=None,
    ttl_hours=DEFAULT_TTL_HOURS,
):
    """Capture observation + interpreted memory for one capability outcome."""
    db = _require_db(db)
    summary, failure_class, tags = classify_capability_result(
        capability=capability,
        ok=ok,
        exit_code=exit_code,
        denied=denied,
        denial_class=denial_class,
        error=error,
    )
    payload = {
        "capability": capability,
        "project_key": project_key,
        "task_id": task_id,
        "goal_id": goal_id,
        "ok": bool(ok) and not denied,
        "denied": bool(denied),
        "denial_class": denial_class,
        "exit_code": exit_code,
        "error": error,
        "failure_class": failure_class,
        "summary": summary,
        "detail": detail,
    }
    obs_id = record_observation(
        db,
        content=json.dumps(payload, sort_keys=True),
        observation_type=OBS_TYPE_CAPABILITY,
        task_id=task_id,
    )
    if confidence is None:
        confidence = 0.9 if denied or not ok else 0.6
    memory = create_memory(
        db,
        observation_id=obs_id,
        summary=summary,
        task_id=task_id,
        goal_id=goal_id,
        project_key=project_key,
        capability=capability,
        failure_class=failure_class,
        tags=tags,
        confidence=confidence,
        ttl_hours=ttl_hours,
    )
    return {"observation_id": obs_id, "memory": memory}


def list_active_memories(
    db,
    *,
    project_key=None,
    capability=None,
    failure_class=None,
    limit=20,
    now=None,
):
    """Retrieve non-expired memories, newest first."""
    db = _require_db(db)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    clauses = ["1=1"]
    params = []
    if project_key is not None:
        clauses.append("project_key = ?")
        params.append(project_key)
    if capability is not None:
        clauses.append("capability = ?")
        params.append(capability)
    if failure_class is not None:
        clauses.append("failure_class = ?")
        params.append(failure_class)

    rows = db.execute(
        f"""SELECT id, observation_id, task_id, goal_id, project_key, capability,
                   failure_class, tags_json, summary, confidence, expires_at,
                   created_at
            FROM agent_memories
            WHERE {' AND '.join(clauses)}
            ORDER BY id DESC
            LIMIT ?""",
        (*params, limit * 3),
    ).fetchall()

    now = now or datetime.now(timezone.utc)
    out = []
    for row in rows:
        mem = _row_to_memory(row)
        if is_expired(mem, now=now):
            continue
        out.append(mem)
        if len(out) >= limit:
            break
    return out


def retrieve_for_planning(
    db,
    *,
    project_key,
    capability=None,
    failure_class=None,
    limit=5,
):
    """Planner-facing retrieval: active memories that may change a plan."""
    if not isinstance(project_key, str) or not project_key.strip():
        raise ValueError("project_key must be a non-empty string")
    memories = list_active_memories(
        db,
        project_key=project_key.strip(),
        capability=capability,
        failure_class=failure_class,
        limit=limit,
    )
    repeated_failures = [
        m
        for m in memories
        if m.get("failure_class") and "success" not in (m.get("tags") or [])
    ]
    return {
        "project_key": project_key.strip(),
        "memories": memories,
        "repeated_failure": len(repeated_failures) >= 2,
        "failure_classes": sorted(
            {
                m["failure_class"]
                for m in repeated_failures
                if m.get("failure_class")
            }
        ),
        "hints": [m["summary"] for m in memories[:limit]],
    }


def expire_and_prune(
    db,
    *,
    project_key=None,
    max_per_project=DEFAULT_MAX_MEMORIES_PER_PROJECT,
    now=None,
):
    """Delete expired rows; cap active memories per project (oldest first)."""
    db = _require_db(db)
    now = now or datetime.now(timezone.utc)
    now_s = now.isoformat()

    if project_key is None:
        cur = db.execute(
            """DELETE FROM agent_memories
               WHERE expires_at IS NOT NULL AND expires_at <= ?""",
            (now_s,),
        )
    else:
        cur = db.execute(
            """DELETE FROM agent_memories
               WHERE project_key = ?
                 AND expires_at IS NOT NULL AND expires_at <= ?""",
            (project_key, now_s),
        )
    expired = cur.rowcount if cur.rowcount is not None else 0

    pruned = 0
    projects = []
    if project_key is not None:
        projects = [project_key]
    else:
        rows = db.execute(
            "SELECT DISTINCT project_key FROM agent_memories "
            "WHERE project_key IS NOT NULL"
        ).fetchall()
        projects = [r[0] if not hasattr(r, "keys") else r["project_key"] for r in rows]

    for key in projects:
        ids = [
            (r[0] if not hasattr(r, "keys") else r["id"])
            for r in db.execute(
                """SELECT id FROM agent_memories
                   WHERE project_key = ?
                   ORDER BY id DESC""",
                (key,),
            ).fetchall()
        ]
        drop = ids[max_per_project:]
        for memory_id in drop:
            db.execute("DELETE FROM agent_memories WHERE id = ?", (memory_id,))
            pruned += 1

    db.commit()
    return {"expired": expired, "pruned": pruned}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ANU Observation / Memory v0.1"
    )
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")

    rec = sub.add_parser("record-capability")
    rec.add_argument("--project", required=True)
    rec.add_argument("--capability", required=True)
    rec.add_argument("--ok", action="store_true")
    rec.add_argument("--denied", action="store_true")
    rec.add_argument("--task-id", type=int, default=None)
    rec.add_argument("--goal-id", type=int, default=None)
    rec.add_argument("--exit-code", type=int, default=None)
    rec.add_argument("--error", default=None)

    ret = sub.add_parser("retrieve")
    ret.add_argument("--project", required=True)
    ret.add_argument("--capability", default=None)
    ret.add_argument("--failure-class", default=None)
    ret.add_argument("--limit", type=int, default=5)

    prune = sub.add_parser("prune")
    prune.add_argument("--project", default=None)
    prune.add_argument("--max-per-project", type=int, default=DEFAULT_MAX_MEMORIES_PER_PROJECT)

    args = parser.parse_args(argv)
    db = db_connect(args.db)
    try:
        migrate(db)
        if args.cmd == "migrate":
            print(json.dumps({"ok": True, "migrated": True}))
            return 0
        if args.cmd == "record-capability":
            result = record_capability_result(
                db,
                project_key=args.project,
                capability=args.capability,
                ok=args.ok and not args.denied,
                denied=args.denied,
                task_id=args.task_id,
                goal_id=args.goal_id,
                exit_code=args.exit_code,
                error=args.error,
            )
            print(json.dumps({"ok": True, "result": result}, indent=2))
            return 0
        if args.cmd == "retrieve":
            result = retrieve_for_planning(
                db,
                project_key=args.project,
                capability=args.capability,
                failure_class=args.failure_class,
                limit=args.limit,
            )
            print(json.dumps({"ok": True, "result": result}, indent=2))
            return 0
        if args.cmd == "prune":
            result = expire_and_prune(
                db,
                project_key=args.project,
                max_per_project=args.max_per_project,
            )
            print(json.dumps({"ok": True, "result": result}, indent=2))
            return 0
        parser.error(f"unknown command: {args.cmd}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
