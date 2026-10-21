#!/usr/bin/env python3
"""ANU Observability v0.1 (V0.9) — structured logs, redaction, metrics, prune."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
LOG_DIR = BASE / "logs"
RUNTIME_LOG = LOG_DIR / "anu.log"
DASHBOARD_LOG = LOG_DIR / "anu-dashboard.log"
MAX_LOG_BYTES = 5_000_000

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+"
    r"|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}"
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def redact(text: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", text or "")


def rotate_log(path: Path, *, max_bytes=MAX_LOG_BYTES):
    path = Path(path)
    if not path.is_file():
        return False
    if path.stat().st_size < max_bytes:
        return False
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rotated = path.with_suffix(path.suffix + f".{stamp}")
    path.replace(rotated)
    path.write_text("", encoding="utf-8")
    return True


def write_json_log(message, *, goal_id=None, task_id=None, level="INFO", path=None):
    path = Path(path) if path else RUNTIME_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate_log(path)
    record = {
        "ts": now_iso(),
        "level": level,
        "goal_id": goal_id,
        "task_id": task_id,
        "message": redact(str(message)),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")
    return record


def goal_metrics(db, goal_id):
    iterations = 0
    try:
        row = db.execute(
            """SELECT COALESCE(SUM(iterations),0), COALESCE(SUM(elapsed_seconds),0)
               FROM autonomy_runs WHERE goal_id = ?""",
            (int(goal_id),),
        ).fetchone()
        iterations = int(row[0])
        elapsed = float(row[1])
    except sqlite3.Error:
        elapsed = 0.0
    tasks_done = db.execute(
        """SELECT COUNT(*) FROM tasks WHERE goal_id = ?
           AND lower(status) IN ('completed','complete','committed','done')""",
        (int(goal_id),),
    ).fetchone()[0]
    failures = db.execute(
        """SELECT COUNT(*) FROM tasks WHERE goal_id = ?
           AND lower(status) IN ('failed','error','rolled_back','blocked')""",
        (int(goal_id),),
    ).fetchone()[0]
    switches = db.execute(
        """SELECT COUNT(*) FROM agent_events
           WHERE goal_id = ? AND event_type = 'LLM_PROVIDER_SWITCH'""",
        (int(goal_id),),
    ).fetchone()[0]
    return {
        "goal_id": int(goal_id),
        "iterations": iterations,
        "elapsed_seconds": elapsed,
        "tasks_done": int(tasks_done),
        "failures": int(failures),
        "provider_switches": int(switches),
    }


def write_daily_summary(db, *, out_dir=None):
    out = Path(out_dir) if out_dir else (BASE / "docs" / "runs")
    out.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = out / f"summary-{day}.json"
    goals = db.execute(
        "SELECT id FROM goals WHERE autonomous = 1 ORDER BY id"
    ).fetchall()
    payload = {
        "day": day,
        "goals": [goal_metrics(db, r[0]) for r in goals],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(path)


def prune_events(db, *, keep_days=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(keep_days))).isoformat()
    try:
        cur = db.execute(
            "DELETE FROM agent_events WHERE created_at < ?",
            (cutoff,),
        )
        db.commit()
        return {"ok": True, "deleted": cur.rowcount, "cutoff": cutoff}
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}


def heartbeat_age_seconds(state_path=None):
    path = Path(state_path) if state_path else (BASE / "runtime" / "anu-state.json")
    if not path.is_file():
        return None
    age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    return max(0.0, float(age))
