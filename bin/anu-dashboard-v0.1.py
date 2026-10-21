#!/usr/bin/env python3
"""ANU Autonomy Dashboard v0.1 — stdlib HTTP UI + JSON API (localhost by default)."""

from __future__ import annotations

import argparse
import hmac
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"
WORKSPACE = BASE / "workspace" / "projects"
RUNTIME_STATE_CANDIDATES = (
    BASE / "var" / "runtime-state.json",
    BASE / "runtime" / "runtime-state.json",
    BASE / "runtime" / "anu-state.json",
)
LOG_CANDIDATES = (
    BASE / "var" / "runtime.log",
    BASE / "logs" / "anu.log",
    BASE / "var" / "anu.log",
)

SECRET_REDACT = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer\s+\S+|password\s*[=:]\s*\S+|"
    r"token\s*[=:]\s*\S+|sk-[a-z0-9]{10,}|ghp_[a-zA-Z0-9]{20,}|"
    r"x-anu-token\s*[=:]\s*\S+)"
)

_MODULES: dict[str, Any] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _module_path(name: str) -> Path:
    for candidate in (BASE / "bin" / name, _HERE / name, _REPO / "bin" / name):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(name)


def load_mod(name: str, module_name: str | None = None):
    key = name
    if key in _MODULES:
        return _MODULES[key]
    path = _module_path(name)
    mod_name = module_name or re.sub(r"[^a-zA-Z0-9_]", "_", path.stem)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MODULES[key] = mod
    return mod


def anu_db():
    return load_mod("anu-db-v0.1.py", "anu_db_dashboard")


def autonomy_state():
    return load_mod("anu-autonomy-state-v0.1.py", "anu_autonomy_state_dashboard")


def autonomy_controller():
    return load_mod("anu-autonomy-controller-v0.1.py", "anu_ctrl_dashboard")


def llm_router():
    return load_mod("anu-llm-router-v0.1.py", "anu_llm_router_dashboard")


def approval_policy():
    return load_mod("anu-auto-approval-policy-v0.1.py", "anu_auto_approval_dashboard")


def approval_service():
    return load_mod("anu-approval-service-v0.1.py", "anu_approval_svc_dashboard")


def db_connect():
    state = autonomy_state()
    db = anu_db().db_connect(DB_FILE)
    state.migrate(db)
    approval_service().migrate(db)
    return db


def read_dashboard_token() -> str | None:
    env = os.environ.get("ANU_DASHBOARD_TOKEN")
    if env and env.strip():
        return env.strip()
    path_env = os.environ.get("ANU_DASHBOARD_TOKEN_FILE")
    if path_env:
        path = Path(path_env)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip() or None
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir:
        for name in ("dashboard-token", "anu-dashboard-token", "ANU_DASHBOARD_TOKEN"):
            p = Path(cred_dir) / name
            if p.is_file():
                return p.read_text(encoding="utf-8").strip() or None
    return None


def token_ok(expected: str | None, provided: str | None) -> bool:
    if not expected:
        return False
    if not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def parse_bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def redact_log_text(text: str) -> str:
    return SECRET_REDACT.sub("[REDACTED]", text or "")


def read_runtime_state() -> dict | None:
    for path in RUNTIME_STATE_CANDIDATES:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return None


def runtime_state_path() -> Path | None:
    for path in RUNTIME_STATE_CANDIDATES:
        if path.is_file():
            return path
    return RUNTIME_STATE_CANDIDATES[0]


def read_log_tail(lines: int = 200) -> str:
    n = max(1, min(int(lines), 2000))
    for path in LOG_CANDIDATES:
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                chunk = min(size, 256_000)
                handle.seek(max(0, size - chunk))
                data = handle.read().decode("utf-8", errors="replace")
            # Fail-soft: never raise on odd line shapes.
            raw_lines = data.splitlines()
            tail = [str(line) for line in raw_lines[-n:]]
            return redact_log_text("\n".join(tail))
        except (OSError, TypeError, ValueError):
            continue
    return ""


def progress_excerpt_for_goal(db, goal_id: int, *, max_chars: int = 4000) -> dict:
    """Best-effort AUTONOMY_PROGRESS.md excerpt for the Active Goal tab."""
    proj = project_for_goal(db, goal_id)
    if proj is None:
        return {"ok": False, "error": "goal has no project"}
    key = proj.get("project_key")
    root = WORKSPACE / key if key else Path(proj.get("workspace") or "")
    path = root / "AUTONOMY_PROGRESS.md"
    if not path.is_file():
        return {"ok": False, "error": "progress file missing", "path": str(path)}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": str(exc), "path": str(path)}
    if len(text) > max_chars:
        text = text[-max_chars:]
    return {
        "ok": True,
        "path": str(path),
        "text": redact_log_text(text),
        "project_key": key,
    }


def format_approval_row(row) -> dict:
    """Normalize approval rows for UI display (clear action / reason)."""
    if isinstance(row, sqlite3.Row):
        rec = dict(row)
    elif isinstance(row, dict):
        rec = dict(row)
    else:
        rec = {
            "id": row[0] if len(row) > 0 else None,
            "task_id": row[1] if len(row) > 1 else None,
            "goal_id": row[2] if len(row) > 2 else None,
            "action": row[3] if len(row) > 3 else None,
            "status": row[4] if len(row) > 4 else None,
            "reason": row[6] if len(row) > 6 else None,
        }
    action = str(rec.get("action") or "").strip()
    reason = str(rec.get("reason") or "").strip()
    # Avoid glued labels like capability_operationsresearch
    if action and reason and not reason.startswith(action):
        label = f"{action} — {reason}"
    else:
        label = action or reason or "(no detail)"
    rec["label"] = label
    rec["action"] = action
    rec["reason"] = reason
    return rec


def guardian_summary(db) -> dict:
    out: dict[str, Any] = {"status": None, "source": None}
    runtime = read_runtime_state()
    if isinstance(runtime, dict) and runtime.get("guardian"):
        out["status"] = runtime.get("guardian")
        out["source"] = "runtime-state"
        return out
    try:
        row = db.execute(
            """SELECT cpu_percent, memory_mb, temperature_c, disk_free_gb, created_at
               FROM resource_usage ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if row is not None:
            if isinstance(row, sqlite3.Row):
                out["metrics"] = dict(row)
            else:
                out["metrics"] = {
                    "cpu_percent": row[0],
                    "memory_mb": row[1],
                    "temperature_c": row[2],
                    "disk_free_gb": row[3],
                    "created_at": row[4],
                }
            out["source"] = "resource_usage"
    except sqlite3.Error:
        pass
    return out


def current_tasks(db, goal_id: int | None = None) -> list[dict]:
    sql = """SELECT t.id, t.goal_id, t.title, t.status, t.updated_at
             FROM tasks t
             WHERE t.status IN ('running','active','in_progress','waiting')"""
    params: list[Any] = []
    if goal_id is not None:
        sql += " AND t.goal_id = ?"
        params.append(int(goal_id))
    sql += " ORDER BY t.updated_at DESC LIMIT 20"
    try:
        rows = db.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for row in rows:
        out.append(dict(row) if isinstance(row, sqlite3.Row) else {
            "id": row[0], "goal_id": row[1], "title": row[2],
            "status": row[3], "updated_at": row[4],
        })
    return out


def list_tasks_for_goal(db, goal_id: int | None) -> list[dict]:
    if goal_id is None:
        rows = db.execute(
            """SELECT id, goal_id, title, status, priority, attempts, created_at, updated_at
               FROM tasks ORDER BY id DESC LIMIT 200"""
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT id, goal_id, title, status, priority, attempts, created_at, updated_at
               FROM tasks WHERE goal_id = ? ORDER BY id""",
            (int(goal_id),),
        ).fetchall()
    return [dict(r) if isinstance(r, sqlite3.Row) else {
        "id": r[0], "goal_id": r[1], "title": r[2], "status": r[3],
        "priority": r[4], "attempts": r[5], "created_at": r[6], "updated_at": r[7],
    } for r in rows]


def list_events(db, since: str | None, limit: int) -> list[dict]:
    lim = max(1, min(int(limit), 500))
    if since:
        rows = db.execute(
            """SELECT id, event_type, message, metadata, goal_id, task_id, created_at
               FROM agent_events WHERE created_at >= ? ORDER BY id DESC LIMIT ?""",
            (since, lim),
        ).fetchall()
    else:
        rows = db.execute(
            """SELECT id, event_type, message, metadata, goal_id, task_id, created_at
               FROM agent_events ORDER BY id DESC LIMIT ?""",
            (lim,),
        ).fetchall()
    out = []
    for row in rows:
        rec = dict(row) if isinstance(row, sqlite3.Row) else {
            "id": row[0], "event_type": row[1], "message": row[2],
            "metadata": row[3], "goal_id": row[4], "task_id": row[5], "created_at": row[6],
        }
        if isinstance(rec.get("metadata"), str):
            try:
                rec["metadata"] = json.loads(rec["metadata"])
            except json.JSONDecodeError:
                pass
        out.append(rec)
    return out


def classify_activity_phase(event_type: str | None, message: str | None = None) -> str:
    """Map runtime signals to Cursor-like activity phases."""
    et = str(event_type or "").upper()
    msg = str(message or "").lower()
    if et in {
        "PLAN_SAVED",
        "GOAL_CREATED",
        "GOAL_STATUS_CHANGED",
        "AUTONOMY_START",
        "AUTONOMY_DECISION",
    }:
        return "planning"
    if et in {
        "MUTATION_TRANSACTION",
        "COMMAND_STARTED",
        "DIFF",
        "GIT_COMMIT",
        "GIT_PUSH",
    } or "mutation" in msg or "writing" in msg:
        return "writing"
    if et in {
        "TASK_VERIFICATION",
        "TASK_FAILED",
        "TASK_COMPLETED",
        "TASK_RECOVERY_POLICY",
        "COMMAND_FINISHED",
        "CAPABILITY_RESULT",
    } or "python_test" in msg or "test" in msg:
        if "python.compile" in msg or "compile" in msg:
            return "writing"
        return "testing"
    if et in {"RESOURCE_CHECK", "RESOURCE_ADMISSION"}:
        return "system"
    if et.startswith("LLM") or et in {"CAPABILITY_REQUEST", "CAPABILITY_ADMITTED", "TASK_STATE"}:
        return "thinking"
    if et.startswith("TASK_") or et.startswith("CAPABILITY_"):
        return "thinking"
    return "thinking"


def activity_for_goal(db, goal_id: int | None, *, limit: int = 120) -> dict:
    """Unified agent replay feed: events + observations + llm_context + diff."""
    if goal_id is None:
        return {"ok": False, "error": "goal required", "items": []}
    lim = max(1, min(int(limit), 400))
    gid = int(goal_id)
    items: list[dict] = []

    task_ids = [
        int(r[0])
        for r in db.execute("SELECT id FROM tasks WHERE goal_id = ?", (gid,)).fetchall()
    ]

    # --- agent_events ---
    try:
        if task_ids:
            placeholders = ",".join("?" * len(task_ids))
            rows = db.execute(
                f"""SELECT id, event_type, message, metadata, goal_id, task_id, created_at
                    FROM agent_events
                    WHERE goal_id = ? OR task_id IN ({placeholders})
                    ORDER BY id DESC LIMIT ?""",
                (gid, *task_ids, lim),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT id, event_type, message, metadata, goal_id, task_id, created_at
                   FROM agent_events WHERE goal_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (gid, lim),
            ).fetchall()
    except sqlite3.Error:
        rows = []

    for row in rows:
        rec = dict(row) if isinstance(row, sqlite3.Row) else {
            "id": row[0], "event_type": row[1], "message": row[2],
            "metadata": row[3], "goal_id": row[4], "task_id": row[5], "created_at": row[6],
        }
        meta = rec.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                pass
        et = rec.get("event_type")
        msg = rec.get("message")
        phase = classify_activity_phase(et, msg)
        detail = ""
        if isinstance(meta, dict):
            # Prefer compact human fields
            for key in ("reason", "decision", "mutation_status", "summary", "operation"):
                if meta.get(key) is not None:
                    detail = str(meta.get(key))
                    break
            if not detail:
                try:
                    detail = json.dumps(meta, default=str)[:600]
                except (TypeError, ValueError):
                    detail = str(meta)[:600]
        items.append({
            "id": f"evt-{rec.get('id')}",
            "source": "event",
            "phase": phase,
            "kind": et,
            "title": msg or et or "event",
            "detail": redact_log_text(detail) if detail else "",
            "task_id": rec.get("task_id"),
            "goal_id": rec.get("goal_id") or gid,
            "created_at": rec.get("created_at"),
        })

    # --- observations ---
    try:
        if task_ids:
            placeholders = ",".join("?" * len(task_ids))
            obs_rows = db.execute(
                f"""SELECT id, task_id, observation_type, content, created_at
                    FROM observations
                    WHERE task_id IN ({placeholders})
                    ORDER BY id DESC LIMIT ?""",
                (*task_ids, lim),
            ).fetchall()
        else:
            obs_rows = []
    except sqlite3.Error:
        obs_rows = []

    for row in obs_rows:
        rec = dict(row) if isinstance(row, sqlite3.Row) else {
            "id": row[0], "task_id": row[1], "observation_type": row[2],
            "content": row[3], "created_at": row[4],
        }
        otype = str(rec.get("observation_type") or "observation")
        content = rec.get("content") or ""
        parsed = None
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
        summary = ""
        if isinstance(parsed, dict):
            summary = str(
                parsed.get("summary")
                or parsed.get("decision")
                or parsed.get("capability")
                or ""
            )
            if not summary:
                summary = json.dumps(parsed, default=str)[:500]
        else:
            summary = str(content)[:500]
        phase = "testing" if "verif" in otype or "test" in otype else "thinking"
        if "capability" in otype:
            phase = "testing" if "test" in summary.lower() else "writing"
        items.append({
            "id": f"obs-{rec.get('id')}",
            "source": "observation",
            "phase": phase,
            "kind": otype,
            "title": otype.replace("_", " "),
            "detail": redact_log_text(summary),
            "task_id": rec.get("task_id"),
            "goal_id": gid,
            "created_at": rec.get("created_at"),
        })

    # --- llm_context (operator + model turns) ---
    try:
        llm_rows = db.execute(
            """SELECT id, goal_id, task_id, role, content, summary, provider, created_at
               FROM llm_context WHERE goal_id = ?
               ORDER BY id DESC LIMIT ?""",
            (gid, min(lim, 80)),
        ).fetchall()
    except sqlite3.Error:
        llm_rows = []

    for row in llm_rows:
        rec = dict(row) if isinstance(row, sqlite3.Row) else {
            "id": row[0], "goal_id": row[1], "task_id": row[2], "role": row[3],
            "content": row[4], "summary": row[5], "provider": row[6], "created_at": row[7],
        }
        role = str(rec.get("role") or "message")
        body = str(rec.get("summary") or rec.get("content") or "")[:1200]
        items.append({
            "id": f"llm-{rec.get('id')}",
            "source": "llm",
            "phase": "thinking",
            "kind": f"llm:{role}",
            "title": f"LLM {role}" + (f" · {rec.get('provider')}" if rec.get("provider") else ""),
            "detail": redact_log_text(body),
            "task_id": rec.get("task_id"),
            "goal_id": gid,
            "created_at": rec.get("created_at"),
        })

    # --- current diff snapshot as writing ---
    try:
        diff = diff_for_goal(db, gid)
        if diff.get("ok") and (diff.get("stat") or diff.get("patch")):
            items.append({
                "id": f"diff-{gid}",
                "source": "diff",
                "phase": "writing",
                "kind": "workspace_diff",
                "title": "Workspace diff",
                "detail": redact_log_text(
                    str(diff.get("stat") or "")[:400]
                    + ("\n" + str(diff.get("patch") or "")[:800]
                       if diff.get("patch") else "")
                ),
                "task_id": None,
                "goal_id": gid,
                "created_at": now_iso(),
            })
    except Exception:
        pass

    def _sort_key(it: dict):
        return str(it.get("created_at") or ""), str(it.get("id") or "")

    items.sort(key=_sort_key, reverse=True)
    items = items[:lim]
    # Chronological for UI (oldest → newest); client can pin scroll to bottom
    items_chrono = list(reversed(items))

    phases: dict[str, int] = {}
    for it in items_chrono:
        phases[it["phase"]] = phases.get(it["phase"], 0) + 1

    try:
        tasks = list_tasks_for_goal(db, gid)
    except sqlite3.Error:
        tasks = []
    return {
        "ok": True,
        "goal_id": gid,
        "items": items_chrono,
        "phases": phases,
        "tasks": [
            {
                "id": t.get("id"),
                "title": t.get("title"),
                "status": t.get("status"),
                "attempts": t.get("attempts"),
            }
            for t in tasks
        ],
    }


def project_for_goal(db, goal_id: int) -> dict | None:
    row = db.execute(
        """SELECT p.project_key, p.workspace, p.repository
           FROM goals g JOIN projects p ON p.id = g.project_id
           WHERE g.id = ?""",
        (int(goal_id),),
    ).fetchone()
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return {"project_key": row[0], "workspace": row[1], "repository": row[2]}


def git_info_for_goal(db, goal_id: int | None) -> dict:
    if goal_id is None:
        return {"ok": False, "error": "goal required"}
    proj = project_for_goal(db, goal_id)
    if proj is None:
        return {"ok": False, "error": "goal has no project"}
    key = proj.get("project_key")
    root = WORKSPACE / key if key else Path(proj.get("workspace") or "")
    if not root.is_dir():
        return {"ok": False, "error": f"workspace missing: {root}", "project_key": key}
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        branch = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=8, env=env, check=False,
        )
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=8, env=env, check=False,
        )
        log = subprocess.run(
            ["git", "-C", str(root), "log", "-5", "--oneline"],
            capture_output=True, text=True, timeout=8, env=env, check=False,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            capture_output=True, text=True, timeout=8, env=env, check=False,
        )
        remote = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=8, env=env, check=False,
        )
        return {
            "ok": branch.returncode == 0,
            "project_key": key,
            "path": str(root),
            "remote": (remote.stdout or "").strip() if remote.returncode == 0 else None,
            "branch": (branch.stdout or "").strip() if branch.returncode == 0 else None,
            "head": (head.stdout or "").strip() if head.returncode == 0 else None,
            "log": (log.stdout or "").strip().splitlines(),
            "status": (status.stdout or "").strip().splitlines(),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc), "project_key": key}


def diff_for_goal(db, goal_id: int | None) -> dict:
    """Best-effort recent mutation / git diff excerpt for Active Goal."""
    if goal_id is None:
        return {"ok": False, "error": "goal required"}
    proj = project_for_goal(db, goal_id)
    if not proj:
        return {"ok": False, "error": "no project"}
    root = Path(proj.get("workspace") or "")
    key = proj.get("project_key")
    if not root.is_dir() and key:
        root = WORKSPACE / key
    if not root.is_dir():
        return {"ok": False, "error": "workspace missing"}
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "diff", "HEAD~1", "--stat"],
            capture_output=True, text=True, timeout=8, env=env, check=False,
        )
        text = (proc.stdout or proc.stderr or "").strip()
        return {"ok": True, "text": text[:8000] or "(no diff)"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}


def matrix_for_goal(db, goal_id: int | None) -> dict:
    """Best-effort last MATRIX_RESULTS.json or live detect for goal workspace."""
    if goal_id is None:
        return {"ok": False, "error": "goal required"}
    proj = project_for_goal(db, goal_id)
    if proj is None:
        return {"ok": False, "error": "goal has no project"}
    key = proj.get("project_key")
    root = Path(proj.get("workspace") or "") if proj.get("workspace") else (WORKSPACE / key)
    if key and not root.is_dir():
        root = WORKSPACE / key
    if not root.is_dir():
        return {"ok": False, "error": f"workspace missing: {root}"}
    artifact = root / ".anu" / "MATRIX_RESULTS.json"
    if artifact.is_file():
        try:
            data = json.loads(artifact.read_text(encoding="utf-8"))
            data["ok"] = True
            data["source"] = "artifact"
            return data
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": str(exc)}
    try:
        mod = load_mod("anu-test-matrix-v0.1.py", "anu_matrix_dash")
        report = mod.detect_matrix(root)
        report["source"] = "detect"
        return report
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def tests_for_goal(db, goal_id: int | None) -> dict:
    clauses = ["event_type LIKE '%test%' OR event_type LIKE '%TEST%'"]
    params: list[Any] = []
    if goal_id is not None:
        clauses.append("goal_id = ?")
        params.append(int(goal_id))
    sql = f"""SELECT id, event_type, message, metadata, created_at
              FROM agent_events WHERE {' AND '.join(clauses)}
              ORDER BY id DESC LIMIT 20"""
    try:
        rows = db.execute(sql, params).fetchall()
    except sqlite3.Error:
        rows = []
    events = []
    for row in rows:
        events.append(dict(row) if isinstance(row, sqlite3.Row) else {
            "id": row[0], "event_type": row[1], "message": row[2],
            "metadata": row[3], "created_at": row[4],
        })
    return {"ok": True, "events": events}


def collect_system() -> dict:
    out: dict[str, Any] = {
        "platform": sys.platform,
        "memory": None,
        "disk": None,
        "loadavg": None,
        "cpu_percent": None,
        "temperature_c": None,
        "uptime_seconds": None,
    }
    if sys.platform.startswith("linux"):
        try:
            mem: dict[str, float] = {}
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    key, value = line.split(":", 1)
                    mem[key] = float(value.strip().split()[0])
            total_kb = mem.get("MemTotal", 0)
            avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
            out["memory"] = {
                "total_mb": round(total_kb / 1024, 2),
                "available_mb": round(avail_kb / 1024, 2),
                "used_mb": round((total_kb - avail_kb) / 1024, 2),
            }
        except OSError:
            pass
        try:
            usage = shutil.disk_usage(str(BASE if BASE.exists() else "/"))
            out["disk"] = {
                "total_gb": round(usage.total / (1024 ** 3), 2),
                "used_gb": round(usage.used / (1024 ** 3), 2),
                "free_gb": round(usage.free / (1024 ** 3), 2),
                "used_percent": round(usage.used / usage.total * 100, 2) if usage.total else None,
            }
        except OSError:
            pass
        try:
            la = os.getloadavg()
            out["loadavg"] = {"1m": la[0], "5m": la[1], "15m": la[2]}
        except (AttributeError, OSError):
            pass
        try:
            with open("/proc/uptime", encoding="utf-8") as handle:
                out["uptime_seconds"] = round(float(handle.read().split()[0]), 2)
        except OSError:
            pass
        thermal_root = Path("/sys/class/thermal")
        try:
            for zone in sorted(thermal_root.glob("thermal_zone*/temp")):
                raw = zone.read_text(encoding="utf-8").strip()
                if raw:
                    out["temperature_c"] = round(int(raw) / 1000.0, 2)
                    break
        except OSError:
            pass
    elif sys.platform == "win32":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total = stat.ullTotalPhys
                avail = stat.ullAvailPhys
                out["memory"] = {
                    "total_mb": round(total / (1024 ** 2), 2),
                    "available_mb": round(avail / (1024 ** 2), 2),
                    "used_mb": round((total - avail) / (1024 ** 2), 2),
                }
        except Exception:
            pass
        try:
            usage = shutil.disk_usage(str(BASE.drive if BASE.drive else "C:\\"))
            out["disk"] = {
                "total_gb": round(usage.total / (1024 ** 3), 2),
                "used_gb": round(usage.used / (1024 ** 3), 2),
                "free_gb": round(usage.free / (1024 ** 3), 2),
                "used_percent": round(usage.used / usage.total * 100, 2) if usage.total else None,
            }
        except OSError:
            pass
        try:
            out["uptime_seconds"] = round(time.time() - ps_boot_time_win(), 2)
        except Exception:
            pass
    return out


def ps_boot_time_win() -> float:
    import ctypes

    GetTickCount64 = ctypes.windll.kernel32.GetTickCount64
    GetTickCount64.restype = ctypes.c_ulonglong
    ms = GetTickCount64()
    return time.time() - (ms / 1000.0)


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any):
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(min(length, 1_000_000))
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON body") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def resolve_project_id(db, project_key: str) -> int:
    row = db.execute(
        "SELECT id FROM projects WHERE project_key = ?",
        (project_key.strip(),),
    ).fetchone()
    if row is None:
        raise LookupError(f"unknown project_key: {project_key}")
    return int(row[0] if not isinstance(row, sqlite3.Row) else row["id"])


def build_checkpoint_snapshot(db, goal_id: int) -> dict:
    state = autonomy_state()
    obj = state.get_objective(db, goal_id)
    tasks = list_tasks_for_goal(db, goal_id)
    ctl = state.effective_control(db, goal_id)
    return {
        "goal_id": goal_id,
        "captured_at": now_iso(),
        "objective": obj,
        "control": ctl,
        "tasks": tasks,
    }


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ANU Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Sans+3:wght@400;600&display=swap" rel="stylesheet"/>
<style>
:root {
  --bg: #081018;
  --bg2: #0e1620;
  --panel: #13202c;
  --panel2: #182838;
  --line: #2c4054;
  --text: #eef4fa;
  --muted: #8fa3b8;
  --accent: #1fb8a8;
  --accent-dim: #147a70;
  --steel: #5b8ab5;
  --warn: #d9a441;
  --bad: #e05252;
  --ok: #34d399;
  --radius: 12px;
  --field-gap: 1.15rem;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  font-family: "Source Sans 3", "IBM Plex Sans", system-ui, sans-serif;
  background:
    radial-gradient(1000px 520px at 8% -8%, rgba(31, 184, 168, .12) 0%, transparent 55%),
    radial-gradient(800px 480px at 100% 0%, rgba(91, 138, 181, .14) 0%, transparent 50%),
    linear-gradient(180deg, #0a121c 0%, var(--bg) 40%, #070d14 100%);
  color: var(--text);
  line-height: 1.5;
  min-height: 100vh;
}
@keyframes pulse-live {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(31, 184, 168, .45); }
  50% { opacity: .75; box-shadow: 0 0 0 6px rgba(31, 184, 168, 0); }
}
@keyframes bar-fill {
  from { transform: scaleX(0); }
  to { transform: scaleX(1); }
}
@keyframes scan-sweep {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
@keyframes fade-up {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
header {
  padding: 1.1rem 1.35rem .8rem;
  background: rgba(14, 22, 32, .94);
  border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 5;
  backdrop-filter: blur(12px);
}
.brand { display: flex; flex-wrap: wrap; gap: .65rem 1.1rem; align-items: center; }
h1 {
  margin: 0;
  font-family: "IBM Plex Sans", sans-serif;
  font-size: 1.4rem;
  font-weight: 700;
  letter-spacing: .01em;
}
.live-dot {
  width: .65rem; height: .65rem; border-radius: 50%;
  background: var(--accent); display: inline-block;
  animation: pulse-live 1.6s ease-out infinite;
}
.live-dot.idle { animation: none; background: var(--muted); opacity: .5; }
.live-dot.stale { background: var(--bad); animation: pulse-live 1s ease-out infinite; }
.meta { color: var(--muted); font-size: .84rem; font-family: "IBM Plex Mono", monospace; }
.controls {
  display: flex; flex-wrap: wrap; gap: .55rem; align-items: center; margin-top: .85rem;
}
.controls label.goal-pick {
  margin-left: auto; display: flex; align-items: center; gap: .5rem;
  font-size: .85rem; color: var(--muted);
}
.controls select#goal-filter { min-width: min(22rem, 70vw); }
.tabs {
  display: flex; gap: .25rem; overflow-x: auto; padding: .6rem 1.35rem 0;
  border-bottom: 1px solid var(--line);
  background: var(--bg2);
}
.tab {
  flex: 0 0 auto;
  border: 0; border-bottom: 2px solid transparent;
  background: transparent; color: var(--muted);
  padding: .65rem 1rem; cursor: pointer; font-weight: 600; font-size: .95rem;
  transition: color .15s ease, border-color .15s ease;
}
.tab:hover { color: var(--text); }
.tab[aria-selected="true"] { color: var(--accent); border-bottom-color: var(--accent); }
main { padding: 1.25rem 1.35rem 2.5rem; max-width: 1280px; margin: 0 auto; }
.panel { display: none; animation: fade-up .28s ease; }
.panel.active { display: block; }
.grid { display: grid; gap: 1.1rem; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
.grid-2 { display: grid; gap: 1.1rem; grid-template-columns: 1.15fr 1fr; }
.grid-assign { display: grid; gap: 1.25rem; grid-template-columns: minmax(0, 1fr) minmax(0, .9fr); }
@media (max-width: 960px) {
  .grid-2, .grid-assign { grid-template-columns: 1fr; }
}
.card {
  background: linear-gradient(165deg, var(--panel2) 0%, var(--panel) 100%);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 1.15rem 1.25rem;
  box-shadow: 0 8px 24px rgba(0,0,0,.22);
}
.card h2 {
  margin: 0 0 .9rem; font-size: .8rem; font-weight: 700;
  letter-spacing: .08em; text-transform: uppercase; color: var(--steel);
}
.card h2 + h2, .card .subhead {
  margin-top: 1.25rem; padding-top: .85rem; border-top: 1px solid var(--line);
}
.sys-head {
  display: flex; align-items: center; justify-content: space-between; gap: .75rem;
  margin-bottom: .85rem;
}
.sys-head h2 { margin: 0; }
.sys-status {
  font-size: .75rem; font-family: "IBM Plex Mono", monospace;
  color: var(--accent); display: flex; align-items: center; gap: .4rem;
}
.sys-status.checking::after {
  content: "";
  width: 3.5rem; height: 2px; border-radius: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  background-size: 200% 100%;
  animation: scan-sweep 1.1s linear infinite;
}
.gauge-block { margin: 0 0 1rem; }
.gauge {
  display: flex; justify-content: space-between; align-items: baseline;
  font-size: .92rem; margin: 0 0 .35rem; gap: .75rem;
}
.gauge .g-label { color: var(--muted); font-weight: 600; font-size: .82rem; }
.gauge .g-value { font-family: "IBM Plex Mono", monospace; font-size: .84rem; }
.bar {
  height: 10px; background: #0a121c; border-radius: 999px; overflow: hidden;
  border: 1px solid #243548; position: relative;
}
.bar > i {
  display: block; height: 100%; width: 0%;
  transform-origin: left center;
  background: linear-gradient(90deg, var(--steel), var(--accent));
  border-radius: 999px;
  transition: width .7s cubic-bezier(.22,.8,.28,1);
}
.bar.checking > i {
  background: linear-gradient(90deg, var(--steel), var(--accent), var(--steel));
  background-size: 200% 100%;
  animation: scan-sweep 1.2s linear infinite;
}
.bar.warn > i { background: linear-gradient(90deg, #b8862e, var(--warn)); }
.bar.hot > i { background: linear-gradient(90deg, #a33, var(--bad)); }
button, input, textarea, select {
  font: inherit; border-radius: 9px; border: 1px solid #3d536b;
  background: #0a121c; color: var(--text); padding: .7rem .85rem;
  width: 100%;
}
button {
  width: auto; cursor: pointer; background: #1e2f42;
  transition: background .15s ease, transform .1s ease;
}
button:hover { background: #274058; }
button:active { transform: translateY(1px); }
button.primary {
  background: var(--accent); border-color: var(--accent-dim);
  color: #042a28; font-weight: 700; padding: .75rem 1.25rem;
}
button.primary:hover { filter: brightness(1.06); }
button.danger { background: #8f2f2f; border-color: #a33; color: #fff; }
button:disabled { opacity: .45; cursor: not-allowed; }
.controls button, .tab, .msg-actions button, .item .actions button { width: auto; }
form#goal-form > button.primary, form#msg-form > button.primary { width: auto; min-width: 10rem; }
input:focus, textarea:focus, select:focus {
  outline: 2px solid rgba(31, 184, 168, .35); outline-offset: 1px; border-color: var(--accent);
}
textarea {
  min-height: 10rem; resize: vertical; line-height: 1.55;
}
pre, .mono {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: .8rem; white-space: pre-wrap; word-break: break-word;
  max-height: 340px; overflow: auto; background: #070d14;
  padding: .85rem; border-radius: 10px; border: 1px solid #1c2a3a;
}
pre.tall { max-height: 460px; }
.list { max-height: 360px; overflow: auto; }
.item { padding: .55rem 0; border-bottom: 1px solid #243548; font-size: .9rem; }
.item:last-child { border-bottom: 0; }
.item .actions { display: flex; flex-wrap: wrap; gap: .45rem; margin-top: .45rem; }
.banner {
  display: none; margin: .85rem 1.35rem 0; padding: .7rem 1rem;
  border-radius: 10px; border: 1px solid #5a4820; background: #2a2212; color: #ffdca8;
}
.banner.show { display: block; }
.banner.stale { border-color: #6a3030; background: #2a1418; color: #ffc9c9; }
.banner.help {
  border-color: #6a5220; background: #2a2210; color: #ffe6a8;
}
.banner.help.show, .banner.show { display: block; }
.goal-actions { margin-bottom: 1rem; }
.goal-actions-row { display: flex; flex-wrap: wrap; gap: .55rem; margin-bottom: .55rem; }
.goal-actions .hint { margin: 0; color: var(--muted); font-size: .88rem; }
.goals td.actions { white-space: nowrap; }
.goals td.actions button { margin-right: .35rem; padding: .25rem .55rem; font-size: .78rem; }
.pill.blocked, .pill.pause { background: rgba(217,164,65,.2); color: var(--warn); }
.pill.needs-help { background: rgba(217,164,65,.28); color: #ffd27a; }
.empty { color: var(--muted); font-size: .9rem; padding: .5rem 0; }
label {
  font-size: .82rem; color: var(--muted); display: block;
  margin: 0 0 .4rem; font-weight: 600; letter-spacing: .02em;
}
form .field { margin: 0 0 var(--field-gap); }
form .field:last-of-type { margin-bottom: 1.25rem; }
.field-row { display: grid; gap: 1rem; grid-template-columns: 1fr 1fr; }
@media (max-width: 640px) { .field-row { grid-template-columns: 1fr; } }
.check-row {
  display: flex; flex-wrap: wrap; gap: 1rem 1.5rem; align-items: center;
  margin: 0 0 1.25rem;
}
.check-row label {
  display: inline-flex; align-items: center; gap: .45rem; margin: 0;
  font-size: .9rem; color: var(--text); font-weight: 500; cursor: pointer;
}
.check-row input[type="checkbox"] { width: auto; margin: 0; accent-color: var(--accent); }
.form-hint {
  margin: 1rem 0 0; padding: .85rem 1rem; border-radius: 10px;
  background: #0a1520; border: 1px dashed var(--line); color: var(--muted); font-size: .88rem;
}
.form-hint code { color: var(--accent); font-family: "IBM Plex Mono", monospace; font-size: .82rem; }
.assign-wrap { max-width: 52rem; }
.msg-card textarea { min-height: 14rem; }
.msg-actions { display: flex; flex-wrap: wrap; gap: .55rem; margin-top: .85rem; }
table.goals { width: 100%; border-collapse: collapse; font-size: .9rem; }
table.goals th, table.goals td { text-align: left; padding: .55rem .4rem; border-bottom: 1px solid var(--line); }
table.goals th { color: var(--muted); font-weight: 600; font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; }
table.goals tr { cursor: pointer; transition: background .12s ease; }
table.goals tr:hover { background: rgba(31, 184, 168, .06); }
.pill {
  display: inline-block; padding: .15rem .5rem; border-radius: 999px;
  font-size: .72rem; font-family: "IBM Plex Mono", monospace; background: #243548;
}
.pill.done, .pill.completed { background: #14352c; color: var(--ok); }
.pill.pending, .pill.active { background: #1e2f44; color: #c5d4e8; }
.pill.blocked, .pill.failed, .pill.cancelled { background: #3d2228; color: #ffb4b4; }
.obj { font-size: 1.02rem; margin-bottom: .35rem; font-weight: 600; }
.workspace-line { color: var(--muted); font-size: .85rem; margin-bottom: 1rem; font-family: "IBM Plex Mono", monospace; }
.status-dot { display: inline-block; width: .55rem; height: .55rem; border-radius: 50%; margin-right: .4rem; background: var(--ok); }
.status-dot.down { background: var(--bad); }
.status-dot.warn { background: var(--warn); }
.activity-toolbar {
  display: flex; flex-wrap: wrap; gap: .5rem; align-items: center;
  margin-bottom: 1rem;
}
.activity-toolbar .chip {
  border: 1px solid var(--line); background: #0a1520; color: var(--muted);
  padding: .35rem .7rem; border-radius: 999px; cursor: pointer; font-size: .82rem; font-weight: 600;
}
.activity-toolbar .chip.on { border-color: var(--accent); color: var(--accent); background: rgba(31,184,168,.1); }
.activity-layout { display: grid; gap: 1.1rem; grid-template-columns: minmax(0, 1.4fr) minmax(240px, .7fr); }
@media (max-width: 960px) { .activity-layout { grid-template-columns: 1fr; } }
.activity-stream {
  max-height: min(70vh, 640px); overflow: auto; padding-right: .25rem;
  scroll-behavior: smooth;
}
.act-item {
  display: grid; grid-template-columns: 6.5rem 1fr; gap: .65rem;
  padding: .75rem 0; border-bottom: 1px solid #243548;
  animation: fade-up .25s ease;
}
.act-item:last-child { border-bottom: 0; }
.act-phase {
  font-size: .68rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
  padding: .25rem .45rem; border-radius: 6px; height: fit-content; text-align: center;
}
.act-phase.thinking { background: #1a2a3d; color: #8ec5ff; }
.act-phase.planning { background: #2a2440; color: #c4b5fd; }
.act-phase.writing { background: #1a3330; color: var(--accent); }
.act-phase.testing { background: #2a2a18; color: var(--warn); }
.act-phase.system { background: #1c2430; color: var(--muted); }
.act-meta { font-size: .75rem; color: var(--muted); font-family: "IBM Plex Mono", monospace; margin-bottom: .25rem; }
.act-title { font-weight: 600; margin-bottom: .3rem; }
.act-detail {
  font-family: "IBM Plex Mono", monospace; font-size: .76rem;
  white-space: pre-wrap; word-break: break-word; color: #c5d4e8;
  background: #070d14; border: 1px solid #1c2a3a; border-radius: 8px;
  padding: .55rem .65rem; max-height: 10rem; overflow: auto;
}
.task-rail .item { display: flex; flex-wrap: wrap; gap: .35rem .55rem; align-items: center; }
@media (max-width: 640px) {
  header { position: static; }
  .tabs { padding-left: .85rem; padding-right: .85rem; }
  main { padding: .9rem; }
  .controls label.goal-pick { margin-left: 0; width: 100%; }
  .controls select#goal-filter { min-width: 0; flex: 1; }
}
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="live-dot" id="live-dot" title="Live refresh"></span>
    <h1>ANU Autonomy</h1>
    <div class="meta" id="hdr-meta">Connecting…</div>
  </div>
  <div class="controls">
    <button type="button" id="btn-pause" title="Pause selected goal and save checkpoint">Pause goal</button>
    <button type="button" id="btn-resume" title="Resume selected goal">Resume goal</button>
    <button type="button" class="danger" id="btn-delete" title="Cancel / delete selected goal">Delete goal</button>
    <button type="button" class="danger" id="btn-stop" title="Stop autonomy (global or goal)">Stop</button>
    <label class="goal-pick">
      <span>Goal</span>
      <select id="goal-filter"><option value="">— select —</option></select>
    </label>
  </div>
</header>
<div class="banner help" id="help-banner"></div>
<div class="banner" id="blocker-banner"></div>
<div class="banner stale" id="stale-banner"></div>
<nav class="tabs" role="tablist" aria-label="Dashboard sections">
  <button type="button" class="tab" role="tab" data-tab="overview" id="tab-overview" aria-selected="true">Overview</button>
  <button type="button" class="tab" role="tab" data-tab="goal" id="tab-goal" aria-selected="false">Active Goal</button>
  <button type="button" class="tab" role="tab" data-tab="runtime" id="tab-runtime" aria-selected="false">Runtime</button>
  <button type="button" class="tab" role="tab" data-tab="activity" id="tab-activity" aria-selected="false">Activity</button>
  <button type="button" class="tab" role="tab" data-tab="approvals" id="tab-approvals" aria-selected="false">Approvals</button>
  <button type="button" class="tab" role="tab" data-tab="assign" id="tab-assign" aria-selected="false">Assign</button>
</nav>
<main>
  <section class="panel active" id="panel-overview" role="tabpanel" data-panel="overview">
    <div class="grid">
      <section class="card">
        <div class="sys-head">
          <h2>System</h2>
          <div class="sys-status" id="sys-status"><span class="live-dot" id="sys-dot"></span><span id="sys-status-text">idle</span></div>
        </div>
        <div id="system-panel"></div>
      </section>
      <section class="card"><h2>Current tasks</h2><div id="task-panel" class="list"></div></section>
      <section class="card" style="grid-column:1/-1"><h2>Goals</h2><div id="goals-table-wrap"></div></section>
    </div>
  </section>
  <section class="panel" id="panel-goal" role="tabpanel" data-panel="goal">
    <div class="goal-actions card" id="goal-actions">
      <div class="goal-actions-row">
        <button type="button" id="btn-goal-pause">Pause &amp; save</button>
        <button type="button" id="btn-goal-resume">Resume</button>
        <button type="button" class="danger" id="btn-goal-delete">Delete goal</button>
      </div>
      <p class="hint" id="goal-control-hint">Select a goal, then Pause to freeze work (checkpoint saved). If the agent is stuck it will pause itself and show a Needs help message.</p>
    </div>
    <div class="grid-2">
      <section class="card">
        <h2>Objective</h2>
        <div class="obj" id="goal-objective"></div>
        <div class="workspace-line" id="goal-workspace"></div>
        <h2 class="subhead">Checklist</h2>
        <div id="checklist-panel" class="list"></div>
      </section>
      <section class="card">
        <h2>Task timeline</h2>
        <div id="timeline-panel" class="list"></div>
      </section>
      <section class="card">
        <h2>Progress file</h2>
        <pre id="progress-panel" class="tall"></pre>
      </section>
      <section class="card msg-card">
        <h2>Message goal</h2>
        <form id="msg-form">
          <div class="field">
            <label for="msg-input">Guidance for the agent</label>
            <textarea id="msg-input" name="message" rows="10" required placeholder="Type operator guidance here — room to think and paste notes…"></textarea>
          </div>
          <button type="submit" class="primary">Send message</button>
        </form>
        <div class="msg-actions">
          <button type="button" id="btn-open-pr">Open PR (gated)</button>
          <button type="button" id="btn-restore-ckpt">Checkpoint restore (gated)</button>
        </div>
        <h2 class="subhead">Task graph</h2>
        <pre id="task-graph-panel" class="tall"></pre>
        <h2 class="subhead">Diff</h2>
        <pre id="diff-panel" class="tall"></pre>
      </section>
    </div>
  </section>
  <section class="panel" id="panel-runtime" role="tabpanel" data-panel="runtime">
    <div class="grid-2">
      <section class="card"><h2>Events</h2><div id="events-panel" class="list"></div></section>
      <section class="card"><h2>LLM providers</h2><div id="llm-panel" class="list"></div></section>
      <section class="card"><h2>Logs</h2><pre id="logs-panel" class="tall"></pre></section>
      <div class="grid" style="gap:.85rem">
        <section class="card"><h2>Git</h2><pre id="git-panel"></pre></section>
        <section class="card"><h2>Tests</h2><div id="tests-panel" class="list"></div></section>
        <section class="card"><h2>Test matrix</h2><pre id="matrix-panel"></pre></section>
      </div>
    </div>
  </section>
  <section class="panel" id="panel-activity" role="tabpanel" data-panel="activity">
    <section class="card">
      <div class="sys-head">
        <h2>Agent activity / replay</h2>
        <div class="sys-status" id="activity-live"><span class="live-dot" id="activity-dot"></span><span id="activity-live-text">idle</span></div>
      </div>
      <p class="empty" style="margin:0 0 .85rem">Live stream of thinking, planning, writing, and testing for the selected goal — closest to a Cursor-style agent transcript.</p>
      <div class="activity-toolbar" id="activity-filters" role="group" aria-label="Phase filters">
        <button type="button" class="chip on" data-phase="all">All</button>
        <button type="button" class="chip on" data-phase="thinking">Thinking</button>
        <button type="button" class="chip on" data-phase="planning">Planning</button>
        <button type="button" class="chip on" data-phase="writing">Writing</button>
        <button type="button" class="chip on" data-phase="testing">Testing</button>
        <button type="button" class="chip" data-phase="system">System</button>
      </div>
      <div class="activity-layout">
        <div id="activity-stream" class="activity-stream" aria-live="polite"></div>
        <div>
          <h2 class="subhead" style="margin-top:0;border-top:0;padding-top:0">Task rail</h2>
          <div id="activity-tasks" class="list task-rail"></div>
          <h2 class="subhead">Phase counts</h2>
          <div id="activity-phases" class="mono"></div>
        </div>
      </div>
    </section>
  </section>
  <section class="panel" id="panel-approvals" role="tabpanel" data-panel="approvals">
    <section class="card"><h2>Pending approvals</h2><div id="approvals-panel" class="list"></div></section>
  </section>
  <section class="panel" id="panel-assign" role="tabpanel" data-panel="assign">
    <div class="assign-wrap">
      <section class="card">
        <h2>New goal</h2>
        <form id="goal-form">
          <div class="field-row">
            <div class="field">
              <label for="f-project">project_key</label>
              <input id="f-project" name="project_key" required placeholder="anu-agent-test" autocomplete="off"/>
            </div>
            <div class="field">
              <label for="f-github">GitHub repo</label>
              <input id="f-github" name="github" value="Sadhu2005/anu-agent-test" placeholder="Sadhu2005/anu-agent-test" autocomplete="off" spellcheck="false"/>
              <p class="empty" style="margin:.35rem 0 0">Format: <code>owner/repo</code> — no spaces (example already filled).</p>
            </div>
          </div>
          <div class="field">
            <label for="f-desc">Description (goal)</label>
            <input id="f-desc" name="description" required placeholder="What should ANU build?"/>
          </div>
          <div class="field">
            <label for="f-obj">Objective</label>
            <textarea id="f-obj" name="objective" rows="5" placeholder="Optional longer objective — use this space for acceptance detail…"></textarea>
          </div>
          <div class="field">
            <label for="f-crit">Criteria (comma-separated)</label>
            <textarea id="f-crit" name="criteria" rows="3" placeholder="unit+integration green, push agent/*, confined to workspace"></textarea>
          </div>
          <div class="check-row">
            <label><input type="checkbox" name="autonomous" checked/> autonomous</label>
            <label><input type="checkbox" name="clone" checked/> clone if missing</label>
          </div>
          <button type="submit" class="primary">Create goal</button>
        </form>
        <p class="form-hint">Lab model: goal + git repo. ANU plans, runs tests (unit → regression), commits, and pushes <code>agent/*</code>. Humans merge on GitHub.</p>
      </section>
    </div>
  </section>
</main>
<script>
(function(){
  const TOKEN_KEY = "anu_dashboard_token";
  const STALE_HEARTBEAT_S = 90;
  let goalsCache = [];
  let autoSelected = false;

  function token(){
    let t = sessionStorage.getItem(TOKEN_KEY);
    if(!t){ t = prompt("ANU dashboard token:"); if(t) sessionStorage.setItem(TOKEN_KEY, t); }
    return t || "";
  }
  function el(id){ return document.getElementById(id); }
  function setText(node, text){ if(node) node.textContent = text == null ? "" : String(text); }
  function clear(node){ if(!node) return; while(node.firstChild) node.removeChild(node.firstChild); }
  function empty(parent, msg){
    const d = document.createElement("div"); d.className = "empty"; setText(d, msg); parent.appendChild(d);
  }
  function addLine(parent, text){
    const d = document.createElement("div"); d.className = "item"; setText(d, text); parent.appendChild(d);
  }
  function pill(status){
    const s = document.createElement("span");
    s.className = "pill " + String(status||"").toLowerCase();
    setText(s, status || "?");
    return s;
  }
  function setChecking(on){
    const sys = el("sys-status");
    const sysText = el("sys-status-text");
    const live = el("live-dot");
    const sysDot = el("sys-dot");
    if(sys) sys.classList.toggle("checking", !!on);
    if(sysText) setText(sysText, on ? "checking…" : "live");
    if(sysDot) sysDot.classList.toggle("idle", !on);
    document.querySelectorAll("#system-panel .bar").forEach(b=>{
      b.classList.toggle("checking", !!on);
    });
    if(live && !live.classList.contains("stale")){
      live.classList.toggle("idle", !on);
    }
  }
  function gauge(parent, label, value, pct){
    const block = document.createElement("div"); block.className = "gauge-block";
    const row = document.createElement("div"); row.className = "gauge";
    const l = document.createElement("span"); l.className = "g-label"; setText(l, label);
    const v = document.createElement("span"); v.className = "g-value"; setText(v, value);
    row.appendChild(l); row.appendChild(v); block.appendChild(row);
    if(pct != null){
      const n = Math.max(0, Math.min(100, Number(pct) || 0));
      const bar = document.createElement("div"); bar.className = "bar";
      if(n >= 90) bar.classList.add("hot");
      else if(n >= 75) bar.classList.add("warn");
      const i = document.createElement("i");
      i.style.width = "0%";
      bar.appendChild(i); block.appendChild(bar);
      parent.appendChild(block);
      requestAnimationFrame(()=>{ i.style.width = n + "%"; });
    } else {
      parent.appendChild(block);
    }
  }
  async function api(path, opts){
    opts = opts || {};
    const headers = Object.assign({"Accept":"application/json", "X-ANU-Token": token()}, opts.headers||{});
    if(opts.body) headers["Content-Type"] = "application/json";
    const res = await fetch(path, Object.assign({}, opts, {headers}));
    if(res.status === 401){ sessionStorage.removeItem(TOKEN_KEY); throw new Error("Unauthorized"); }
    const data = await res.json().catch(()=>({}));
    if(!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }
  function selectedGoal(){
    const v = el("goal-filter").value.trim();
    return v ? parseInt(v, 10) : null;
  }
  function setTab(name){
    document.querySelectorAll(".tab").forEach(t=>{
      const on = t.getAttribute("data-tab") === name;
      t.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll(".panel").forEach(p=>{
      p.classList.toggle("active", p.getAttribute("data-panel") === name);
    });
    if(location.hash !== "#" + name) location.hash = name;
  }
  function fillGoalSelect(goals){
    const sel = el("goal-filter");
    const prev = sel.value;
    clear(sel);
    const opt0 = document.createElement("option"); opt0.value = ""; setText(opt0, "— select —");
    sel.appendChild(opt0);
    (goals||[]).forEach(g=>{
      const o = document.createElement("option");
      o.value = String(g.id);
      const desc = (g.description || g.objective || "").slice(0, 48);
      const flag = g.needs_help ? " needs-help" : "";
      setText(o, "#" + g.id + " [" + (g.status||"?") + flag + "] " + desc);
      sel.appendChild(o);
    });
    if(prev && [].some.call(sel.options, o => o.value === prev)) sel.value = prev;
    else if(!autoSelected && goals && goals.length){
      const active = goals.find(g => (g.status||"") === "active") || goals[0];
      sel.value = String(active.id);
      autoSelected = true;
    }
  }
  function renderGoalsTable(goals){
    const wrap = el("goals-table-wrap"); clear(wrap);
    if(!goals || !goals.length){ empty(wrap, "No autonomous goals yet."); return; }
    const table = document.createElement("table"); table.className = "goals";
    const thead = document.createElement("thead");
    const hr = document.createElement("tr");
    ["ID","Status","Help","Description","Actions"].forEach(h=>{
      const th = document.createElement("th"); setText(th, h); hr.appendChild(th);
    });
    thead.appendChild(hr); table.appendChild(thead);
    const tb = document.createElement("tbody");
    goals.forEach(g=>{
      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.onclick = ()=>{ el("goal-filter").value = String(g.id); setTab("goal"); tick(); };
      const td1 = document.createElement("td"); setText(td1, "#" + g.id);
      const td2 = document.createElement("td");
      td2.appendChild(pill(g.status));
      if(g.control && g.control !== "run"){
        const c = pill(g.control);
        c.style.marginLeft = ".35rem";
        td2.appendChild(c);
      }
      const tdHelp = document.createElement("td");
      if(g.needs_help){
        tdHelp.appendChild(pill("needs help"));
      } else {
        setText(tdHelp, "—");
      }
      const td3 = document.createElement("td"); setText(td3, g.description || g.objective || "");
      const tdA = document.createElement("td"); tdA.className = "actions";
      const mk = (label, action, cls)=>{
        const b = document.createElement("button");
        setText(b, label);
        if(cls) b.className = cls;
        b.onclick = (ev)=>{ ev.stopPropagation(); control(action, g.id); };
        return b;
      };
      if((g.status||"") === "completed"){
        tdA.appendChild(mk("Delete", "delete", "danger"));
      } else if((g.status||"") === "cancelled"){
        tdA.appendChild(mk("Delete", "delete", "danger"));
      } else {
        tdA.appendChild(mk("Pause", "pause"));
        tdA.appendChild(mk("Resume", "resume"));
        tdA.appendChild(mk("Delete", "delete", "danger"));
      }
      tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(tdHelp);
      tr.appendChild(td3); tr.appendChild(tdA);
      tb.appendChild(tr);
    });
    table.appendChild(tb); wrap.appendChild(table);
  }
  async function refreshOverview(st){
    const ctl = (st && st.control) || {};
    const age = st && st.heartbeat_age_s;
    const ageTxt = age != null ? ("heartbeat age " + age + "s") : "heartbeat n/a";
    setText(el("hdr-meta"), "control=" + (ctl.state||"run") + " · goals=" + ((st && st.autonomous_goals)||[]).length + " · " + ageTxt);
    const live = el("live-dot");
    const stale = el("stale-banner");
    if(age != null && age > STALE_HEARTBEAT_S){
      stale.classList.add("show");
      setText(stale, "Runtime heartbeat is stale (" + age + "s). Check anu-agent / anu-runtime.");
      if(live){ live.classList.add("stale"); live.classList.remove("idle"); }
    } else {
      stale.classList.remove("show");
      if(live) live.classList.remove("stale");
    }
    const tasks = el("task-panel"); clear(tasks);
    const cur = (st && st.current_tasks) || [];
    if(!cur.length) empty(tasks, "No running or waiting tasks.");
    else cur.forEach(t=> addLine(tasks, "#" + t.id + " " + (t.title||"") + " [" + t.status + "]"));

    const goalsResp = await api("/api/goals");
    goalsCache = goalsResp.goals || [];
    fillGoalSelect(goalsCache);
    renderGoalsTable(goalsCache);
  }
  async function refreshSystem(){
    const s = await api("/api/system");
    const p = el("system-panel"); clear(p);
    if(s.memory){
      const usedPct = s.memory.total_mb ? (s.memory.used_mb/s.memory.total_mb*100) : null;
      gauge(p, "RAM used", s.memory.used_mb+" / "+s.memory.total_mb+" MB", usedPct);
    }
    if(s.disk){
      gauge(p, "Disk used", s.disk.used_gb+" / "+s.disk.total_gb+" GB", s.disk.used_percent);
    }
    if(s.loadavg){
      const l1 = Number(s.loadavg["1m"]);
      const loadPct = isFinite(l1) ? Math.min(100, (l1 / 4) * 100) : null;
      gauge(p, "Load (1/5/15m)", s.loadavg["1m"]+" / "+s.loadavg["5m"]+" / "+s.loadavg["15m"], loadPct);
    }
    if(s.temperature_c != null){
      const t = Number(s.temperature_c);
      const tPct = isFinite(t) ? Math.min(100, Math.max(0, (t / 85) * 100)) : null;
      gauge(p, "Temp °C", s.temperature_c, tPct);
    }
    if(s.uptime_seconds != null){
      const u = Number(s.uptime_seconds);
      const days = isFinite(u) ? (u / 86400).toFixed(1) + " d" : String(s.uptime_seconds);
      gauge(p, "Uptime", days + " (" + s.uptime_seconds + " s)", null);
    }
  }
  async function refreshActiveGoal(){
    const gid = selectedGoal();
    const objEl = el("goal-objective");
    const cl = el("checklist-panel");
    const tl = el("timeline-panel");
    const prog = el("progress-panel");
    const banner = el("blocker-banner");
    const help = el("help-banner");
    const hint = el("goal-control-hint");
    if(!gid){
      setText(objEl, "Select a goal in the header.");
      clear(cl); empty(cl, "No goal selected.");
      clear(tl); empty(tl, "No goal selected.");
      setText(prog, "");
      banner.classList.remove("show");
      if(help) help.classList.remove("show");
      return;
    }
    const resp = await api("/api/goals/"+gid);
    const g = resp.goal || resp;
    const ctl = g.control || {};
    const ctlState = ctl.state || "run";
    setText(objEl, (g.objective || g.description || "(no objective)")
      + " · status " + (g.status||"?")
      + " · control " + ctlState);
    const wsEl = el("goal-workspace");
    if(wsEl){
      const bits = [];
      if(g.workspace) bits.push("workspace: " + g.workspace);
      if(g.repository) bits.push("repo: " + g.repository);
      if(g.project_key) bits.push("project: " + g.project_key);
      setText(wsEl, bits.join(" · ") || "");
    }
    clear(cl);
    const checklist = g.checklist || [];
    if(!checklist.length) empty(cl, "No checklist items.");
    else checklist.forEach(c=>{
      const row = document.createElement("div"); row.className = "item";
      row.appendChild(pill(c.status));
      const span = document.createElement("span");
      setText(span, " " + (c.item||""));
      row.appendChild(span);
      if(c.evidence){
        const ev = document.createElement("div");
        ev.className = "mono";
        ev.style.marginTop = ".25rem";
        setText(ev, String(c.evidence).slice(0, 240));
        row.appendChild(ev);
      }
      cl.appendChild(row);
    });
    const blockers = checklist.filter(c=>c.status==="blocked").map(c=>c.item);
    if(blockers.length){ banner.classList.add("show"); setText(banner, "Blockers: "+blockers.join("; ")); }
    else banner.classList.remove("show");

    const curTasks = g.current_tasks || [];
    const looksStuck = (g.status==="active" && ctlState==="run" && !curTasks.length);
    if(help){
      if(g.needs_help || g.help_message){
        help.classList.add("show");
        setText(help, "Needs help — " + (g.help_message || "Goal is paused. Send a message or Resume when ready."));
      } else if(looksStuck){
        help.classList.add("show");
        setText(help, "Looks stuck — goal #"+gid+" is active but has no running tasks. Use Pause & save (freezes state), send guidance, or Delete.");
      } else {
        help.classList.remove("show");
      }
    }
    if(hint){
      if(g.needs_help){
        setText(hint, "Agent paused and asked for help. Fix the issue (or send a Message), then Resume — or Delete to cancel.");
      } else if(looksStuck){
        setText(hint, "No running work on an active goal. Pause & save to freeze, then investigate Activity.");
      } else {
        setText(hint, "Pause & save freezes this goal and writes a checkpoint. Resume continues. Delete permanently removes the goal from the brain (repo files on disk/GitHub stay).");
      }
    }

    const tasks = await api("/api/tasks?goal="+gid);
    clear(tl);
    const list = tasks.tasks || [];
    if(!list.length) empty(tl, "No tasks for this goal.");
    else list.forEach(t=>{
      const row = document.createElement("div"); row.className = "item";
      row.appendChild(pill(t.status));
      const span = document.createElement("span");
      setText(span, " #" + t.id + " " + (t.title||"") + (t.attempts!=null ? " · attempts "+t.attempts : ""));
      row.appendChild(span);
      if(t.evidence || t.title){
        const link = document.createElement("div");
        link.className = "mono";
        setText(link, String(t.evidence||t.title||"").slice(0,160));
        row.appendChild(link);
      }
      tl.appendChild(row);
    });
    const graph = el("task-graph-panel");
    if(graph){
      setText(graph, list.map(t=> (t.id+" ["+t.status+"] "+(t.title||""))).join("\n") || "No tasks.");
    }
    const diff = el("diff-panel");
    if(diff){
      try{
        const d = await api("/api/diff?goal="+gid);
        setText(diff, d.text || d.error || "(no recent mutation diff)");
      }catch(err){ setText(diff, String(err.message||err)); }
    }

    const pr = await api("/api/goals/"+gid+"/progress");
    setText(prog, pr.ok ? (pr.text||"") : (pr.error || "No progress file."));
  }
  function addEventLine(parent, e){
    const row = document.createElement("div"); row.className = "item";
    const kind = String(e.event_type||"");
    const msg = String(e.message||"");
    const bad = /fail|error|denied|blocked/i.test(kind+" "+msg);
    const warn = /warn|resource_check/i.test(kind) && /abnormal|high|critical/i.test(msg);
    const badge = document.createElement("span");
    badge.className = "pill " + (bad ? "failed" : (warn ? "pending" : "done"));
    setText(badge, kind || "event");
    row.appendChild(badge);
    const span = document.createElement("span");
    setText(span, " " + (e.created_at||"") + " · " + msg);
    row.appendChild(span);
    parent.appendChild(row);
  }
  async function refreshRuntime(){
    const ev = await api("/api/events?limit=50");
    const p = el("events-panel"); clear(p);
    const events = ev.events || [];
    if(!events.length) empty(p, "No events yet.");
    else events.forEach(e=> addEventLine(p, e));

    const lg = await api("/api/logs?tail=160");
    setText(el("logs-panel"), lg.text || "(no log file)");

    const llm = await api("/api/llm");
    const lp = el("llm-panel"); clear(lp);
    const providers = llm.providers || [];
    if(!providers.length) empty(lp, "No providers configured.");
    else providers.forEach(pr=>{
      const row = document.createElement("div"); row.className = "item";
      const dot = document.createElement("span");
      dot.className = "status-dot" + (pr.available ? "" : " down");
      row.appendChild(dot);
      const span = document.createElement("span");
      setText(span, (pr.provider||"?") + " — " + (pr.available ? "ok" : "down") + (pr.why ? (" · "+pr.why) : ""));
      row.appendChild(span);
      lp.appendChild(row);
    });

    const gid = selectedGoal();
    const tp = el("tests-panel"); clear(tp);
    const gp = el("git-panel");
    if(!gid){
      empty(tp, "Select a goal to see tests.");
      setText(gp, "Select a goal to see git status.");
      return;
    }
    const tests = await api("/api/tests?goal="+gid);
    const te = tests.events || [];
    if(!te.length) empty(tp, "No test events for this goal.");
    else te.forEach(e=> addLine(tp, (e.created_at||"")+" "+(e.event_type||"")+": "+(e.message||"")));
    const git = await api("/api/git?goal="+gid);
    setText(gp, git.ok
      ? [(git.branch||"")+" @ "+(git.head||""), "remote: "+(git.remote||"origin"), ...(git.log||[]), ...(git.status||[])].filter(Boolean).join("\n")
      : (git.error || "n/a"));
    const mp = el("matrix-panel");
    if(mp){
      try{
        const mx = await api("/api/matrix?goal="+gid);
        setText(mp, mx.ok
          ? JSON.stringify(mx.summary || mx.results || mx, null, 2)
          : (mx.error || "n/a"));
      }catch(err){ setText(mp, String(err.message||err)); }
    }
  }
  async function refreshApprovals(){
    const appr = await api("/api/approvals");
    const ap = el("approvals-panel"); clear(ap);
    const list = appr.approvals || [];
    if(!list.length){ empty(ap, "No pending approvals."); return; }
    list.forEach(a=>{
      const row = document.createElement("div"); row.className = "item";
      const label = a.label || ((a.action||"") + (a.reason ? (" — "+a.reason) : ""));
      const text = document.createElement("div");
      setText(text, "#" + a.id + " task=" + (a.task_id||"?") + (a.goal_id!=null ? (" goal="+a.goal_id) : "") + " · " + label);
      row.appendChild(text);
      const actions = document.createElement("div"); actions.className = "actions";
      const yes = document.createElement("button"); setText(yes,"Approve"); yes.className="primary";
      yes.onclick=()=>decide(a.id,"approve");
      const no = document.createElement("button"); setText(no,"Deny"); no.className="danger";
      no.onclick=()=>decide(a.id,"deny");
      actions.appendChild(yes); actions.appendChild(no); row.appendChild(actions);
      ap.appendChild(row);
    });
  }
  async function decide(id, action){
    await api("/api/approvals/"+id, {method:"POST", body: JSON.stringify({action})});
    refreshApprovals();
  }
  async function control(action, goalId){
    const gid = goalId != null ? goalId : selectedGoal();
    if((action==="pause" || action==="resume" || action==="delete") && !gid){
      alert("Select a goal first (header dropdown), then " + action + ".");
      return;
    }
    if(action==="stop" && !confirm("Stop autonomy for " + (gid ? ("goal #"+gid) : "all goals") + "?")) return;
    if(action==="delete" && !confirm("Delete goal #"+gid+" permanently? Removes its tasks, events, and memory from the brain. Shared repo files and GitHub agent/* branches stay (delete those on GitHub if you want).")) return;
    if(action==="pause" && !confirm("Pause goal #"+gid+" and save a checkpoint?")) return;
    try{
      const res = await api("/api/control", {
        method:"POST",
        body: JSON.stringify({action, goal_id: gid||undefined})
      });
      if(res && res.help_message){
        const hb = el("help-banner");
        if(hb){ hb.classList.add("show"); setText(hb, res.help_message); }
      }
      if(action==="delete"){
        autoSelected = false;
        el("goal-filter").value = "";
        if(res && res.purge && res.purge.ok && res.purge.deleted){
          alert("Goal #"+gid+" deleted. Brain work/memory removed.");
        } else {
          const err = (res && res.purge && (res.purge.error || JSON.stringify(res.purge.fk_errors||[]))) || "delete failed";
          alert("Delete did not remove goal #"+gid+": " + err);
        }
      } else if(gid){
        el("goal-filter").value = String(gid);
      }
    }catch(err){
      alert((action+" failed: ") + (err && err.message ? err.message : err));
    }
    tick();
  }
  el("btn-pause").onclick=()=>control("pause");
  el("btn-resume").onclick=()=>control("resume");
  el("btn-stop").onclick=()=>control("stop");
  if(el("btn-delete")) el("btn-delete").onclick=()=>control("delete");
  if(el("btn-goal-pause")) el("btn-goal-pause").onclick=()=>control("pause");
  if(el("btn-goal-resume")) el("btn-goal-resume").onclick=()=>control("resume");
  if(el("btn-goal-delete")) el("btn-goal-delete").onclick=()=>control("delete");
  el("goal-filter").onchange=()=>tick();
  document.querySelectorAll(".tab").forEach(t=>{
    t.onclick=()=>setTab(t.getAttribute("data-tab"));
  });
  function cleanGithub(raw){
    let s = (raw||"").toString().trim();
    if(!s) return "";
    s = s.replace(/^(github|repo|url)\s*[:=]?\s*/i, "");
    s = s.replace(/\s+/g, "");
    const m = s.match(/([A-Za-z0-9][A-Za-z0-9._-]*)\/([A-Za-z0-9][A-Za-z0-9._-]*?)(?:\.git)?\/?$/i);
    if(m) return m[1] + "/" + m[2];
    return s;
  }
  el("goal-form").onsubmit=async (e)=>{
    e.preventDefault();
    const btn = e.target.querySelector('button[type="submit"]');
    const fd = new FormData(e.target);
    const githubIn = el("f-github");
    const cleaned = cleanGithub(fd.get("github"));
    if(githubIn && cleaned) githubIn.value = cleaned;
    const critRaw = (fd.get("criteria")||"").toString().trim();
    const body = {
      project_key: (fd.get("project_key")||"").toString().trim(),
      description: (fd.get("description")||"").toString().trim(),
      objective: (fd.get("objective")||"").toString().trim() || undefined,
      github: cleaned || undefined,
      clone: !!fd.get("clone"),
      autonomous: !!fd.get("autonomous")
    };
    if(critRaw) body.criteria = critRaw.split(",").map(s=>s.trim()).filter(Boolean);
    if(btn){ btn.disabled = true; setText(btn, "Creating…"); }
    try{
      const res = await api("/api/goals", {method:"POST", body: JSON.stringify(body)});
      const gid = res.goal && res.goal.id;
      alert("Created goal #" + gid + " — watch Active Goal + Runtime.");
      autoSelected = false;
      if(gid){ el("goal-filter").value = String(gid); }
      setTab("goal");
      tick();
    }catch(err){
      alert("Create failed: " + (err && err.message ? err.message : err));
    }finally{
      if(btn){ btn.disabled = false; setText(btn, "Create goal"); }
    }
  };
  el("msg-form").onsubmit=async (e)=>{
    e.preventDefault();
    const gid = selectedGoal();
    if(!gid){ alert("Select a goal first."); return; }
    const fd = new FormData(e.target);
    await api("/api/goals/"+gid+"/message", {method:"POST", body: JSON.stringify({message: fd.get("message")})});
    e.target.reset();
    alert("Message sent to goal " + gid);
  };
  if(el("btn-open-pr")) el("btn-open-pr").onclick=async ()=>{
    const gid = selectedGoal();
    if(!gid){ alert("Select a goal first."); return; }
    const res = await api("/api/pr", {method:"POST", body: JSON.stringify({goal_id: gid})});
    alert(res.needs_approval ? ("PR gated: "+(res.reason||"needs approval")) : JSON.stringify(res.payload||res));
  };
  if(el("btn-restore-ckpt")) el("btn-restore-ckpt").onclick=async ()=>{
    const gid = selectedGoal();
    if(!gid){ alert("Select a goal first."); return; }
    const res = await api("/api/checkpoint/restore", {method:"POST", body: JSON.stringify({goal_id: gid})});
    alert(res.error || ("restore: "+(res.ok?"queued for human confirm":"n/a")));
  };
  let activityCache = [];
  let activityPhases = {};
  const phaseOn = { all:true, thinking:true, planning:true, writing:true, testing:true, system:false };
  function phaseEnabled(ph){
    if(phaseOn.all) return ph === "system" ? !!phaseOn.system : true;
    return !!phaseOn[ph];
  }
  function renderActivityStream(){
    const stream = el("activity-stream");
    if(!stream) return;
    const nearBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 80;
    clear(stream);
    const items = (activityCache||[]).filter(it => phaseEnabled(it.phase||"thinking"));
    if(!items.length){
      empty(stream, selectedGoal()
        ? "No activity for this filter yet — wait for the next scheduler cycle."
        : "Select a goal in the header to replay agent activity.");
      return;
    }
    items.forEach(it=>{
      const row = document.createElement("div"); row.className = "act-item";
      const phase = document.createElement("div");
      phase.className = "act-phase " + (it.phase||"thinking");
      setText(phase, it.phase||"thinking");
      const body = document.createElement("div");
      const meta = document.createElement("div"); meta.className = "act-meta";
      setText(meta, (it.created_at||"") + (it.task_id!=null ? (" · task #"+it.task_id) : "") + " · " + (it.source||"") + " · " + (it.kind||""));
      const title = document.createElement("div"); title.className = "act-title";
      setText(title, it.title||it.kind||"activity");
      body.appendChild(meta); body.appendChild(title);
      if(it.detail){
        const det = document.createElement("div"); det.className = "act-detail";
        setText(det, it.detail);
        body.appendChild(det);
      }
      row.appendChild(phase); row.appendChild(body);
      stream.appendChild(row);
    });
    if(nearBottom) stream.scrollTop = stream.scrollHeight;
  }
  async function refreshActivity(){
    const liveText = el("activity-live-text");
    const liveDot = el("activity-dot");
    const gid = selectedGoal();
    const tasksEl = el("activity-tasks");
    const phasesEl = el("activity-phases");
    if(!gid){
      activityCache = [];
      activityPhases = {};
      renderActivityStream();
      if(tasksEl){ clear(tasksEl); empty(tasksEl, "Select a goal."); }
      if(phasesEl) setText(phasesEl, "");
      if(liveText) setText(liveText, "idle");
      if(liveDot) liveDot.classList.add("idle");
      return;
    }
    if(liveText) setText(liveText, "replaying…");
    if(liveDot) liveDot.classList.remove("idle");
    const data = await api("/api/activity?goal="+gid+"&limit=180");
    activityCache = data.items || [];
    activityPhases = data.phases || {};
    renderActivityStream();
    if(tasksEl){
      clear(tasksEl);
      const tasks = data.tasks || [];
      if(!tasks.length) empty(tasksEl, "No tasks.");
      else tasks.forEach(t=>{
        const row = document.createElement("div"); row.className = "item";
        row.appendChild(pill(t.status));
        const span = document.createElement("span");
        setText(span, " #"+t.id+" "+(t.title||"")+(t.attempts!=null ? " · attempts "+t.attempts : ""));
        row.appendChild(span);
        tasksEl.appendChild(row);
      });
    }
    if(phasesEl){
      const parts = Object.keys(activityPhases).sort().map(k => k+": "+activityPhases[k]);
      setText(phasesEl, parts.join("\n") || "—");
    }
    if(liveText) setText(liveText, (activityCache.length||0)+" steps");
  }
  const filt = el("activity-filters");
  if(filt){
    filt.querySelectorAll(".chip").forEach(btn=>{
      btn.onclick=()=>{
        const ph = btn.getAttribute("data-phase");
        if(ph === "all"){
          const turnOn = !phaseOn.all;
          phaseOn.all = turnOn;
          ["thinking","planning","writing","testing"].forEach(k => { phaseOn[k] = turnOn; });
        } else {
          phaseOn[ph] = !phaseOn[ph];
          phaseOn.all = ["thinking","planning","writing","testing"].every(k => phaseOn[k]);
        }
        filt.querySelectorAll(".chip").forEach(c=>{
          const p = c.getAttribute("data-phase");
          c.classList.toggle("on", !!phaseOn[p]);
        });
        renderActivityStream();
      };
    });
  }
  async function tick(){
    setChecking(true);
    try{
      const st = await api("/api/status");
      await Promise.all([
        refreshOverview(st),
        refreshSystem(),
        refreshActiveGoal(),
        refreshRuntime(),
        refreshActivity(),
        refreshApprovals()
      ]);
    }catch(err){
      setText(el("hdr-meta"), "Error: "+err.message);
      const live = el("live-dot");
      if(live){ live.classList.add("stale"); }
      const sysText = el("sys-status-text");
      if(sysText) setText(sysText, "error");
    }finally{
      setTimeout(()=>setChecking(false), 420);
    }
  }
  const hash = (location.hash||"#overview").replace("#","");
  setTab(["overview","goal","runtime","activity","approvals","assign"].indexOf(hash)>=0 ? hash : "overview");
  tick();
  setInterval(tick, 3000);
})();
</script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ANUDashboard/0.1"
    expected_token: str | None = None

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _provided_token(self) -> str | None:
        bearer = parse_bearer(self.headers.get("Authorization"))
        header = self.headers.get("X-ANU-Token")
        return bearer or (header.strip() if header else None)

    def _auth_api(self, *, require_csrf: bool = False) -> bool:
        tok = self._provided_token()
        if not token_ok(self.expected_token, tok):
            json_response(self, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return False
        if require_csrf:
            csrf = self.headers.get("X-ANU-Token")
            if not csrf or not token_ok(self.expected_token, csrf.strip()):
                json_response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "csrf token required"})
                return False
        return True

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not path.startswith("/api/"):
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return

        if not self._auth_api():
            return

        try:
            if path == "/api/health":
                self._handle_health()
            elif path == "/api/status":
                self._handle_status()
            elif path == "/api/system":
                json_response(self, HTTPStatus.OK, {"ok": True, **collect_system()})
            elif path == "/api/goals":
                self._handle_goals_list()
            elif path.startswith("/api/goals/") and path.endswith("/progress"):
                # /api/goals/<id>/progress
                parts = [p for p in path.split("/") if p]
                if len(parts) == 4 and parts[2].isdigit():
                    self._handle_goal_progress(int(parts[2]))
                else:
                    json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            elif path.startswith("/api/goals/") and path.count("/") == 3:
                gid = path.split("/")[-1]
                if gid.isdigit():
                    self._handle_goal_detail(int(gid))
                else:
                    json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            elif path == "/api/tasks":
                goal = qs.get("goal", [None])[0]
                db = db_connect()
                try:
                    gid = int(goal) if goal else None
                    json_response(self, HTTPStatus.OK, {"ok": True, "tasks": list_tasks_for_goal(db, gid)})
                finally:
                    db.close()
            elif path == "/api/events":
                since = qs.get("since", [None])[0]
                limit = int(qs.get("limit", ["100"])[0])
                db = db_connect()
                try:
                    json_response(self, HTTPStatus.OK, {"ok": True, "events": list_events(db, since, limit)})
                finally:
                    db.close()
            elif path == "/api/logs":
                tail = int(qs.get("tail", ["200"])[0])
                json_response(self, HTTPStatus.OK, {"ok": True, "text": read_log_tail(tail)})
            elif path == "/api/llm":
                db = db_connect()
                try:
                    router = llm_router()
                    json_response(self, HTTPStatus.OK, router.status(db))
                finally:
                    db.close()
            elif path == "/api/git":
                goal = qs.get("goal", [None])[0]
                db = db_connect()
                try:
                    gid = int(goal) if goal else None
                    json_response(self, HTTPStatus.OK, git_info_for_goal(db, gid))
                finally:
                    db.close()
            elif path == "/api/tests":
                goal = qs.get("goal", [None])[0]
                db = db_connect()
                try:
                    gid = int(goal) if goal else None
                    json_response(self, HTTPStatus.OK, tests_for_goal(db, gid))
                finally:
                    db.close()
            elif path == "/api/matrix":
                goal = qs.get("goal", [None])[0]
                db = db_connect()
                try:
                    gid = int(goal) if goal else None
                    json_response(self, HTTPStatus.OK, matrix_for_goal(db, gid))
                finally:
                    db.close()
            elif path == "/api/diff":
                goal = qs.get("goal", [None])[0]
                db = db_connect()
                try:
                    gid = int(goal) if goal else None
                    json_response(self, HTTPStatus.OK, diff_for_goal(db, gid))
                finally:
                    db.close()
            elif path == "/api/activity":
                goal = qs.get("goal", [None])[0]
                limit = int(qs.get("limit", ["150"])[0])
                db = db_connect()
                try:
                    gid = int(goal) if goal else None
                    json_response(
                        self,
                        HTTPStatus.OK,
                        activity_for_goal(db, gid, limit=limit),
                    )
                finally:
                    db.close()
            elif path == "/api/approvals":
                db = db_connect()
                try:
                    svc = approval_service()
                    raw = svc.list_pending(db) or []
                    approvals = [format_approval_row(a) for a in raw]
                    json_response(self, HTTPStatus.OK, {"ok": True, "approvals": approvals})
                finally:
                    db.close()
            else:
                json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
        except Exception as exc:
            json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not path.startswith("/api/"):
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return

        if not self._auth_api(require_csrf=True):
            return

        try:
            body = read_json_body(self)
            if path == "/api/control":
                self._handle_control(body)
            elif path.startswith("/api/approvals/") and path.count("/") == 3:
                aid = path.split("/")[-1]
                if not aid.isdigit():
                    json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid approval id"})
                    return
                self._handle_approval(int(aid), body)
            elif path == "/api/goals":
                self._handle_create_goal(body)
            elif path.startswith("/api/goals/") and path.endswith("/message"):
                # /api/goals/<id>/message
                parts = [p for p in path.split("/") if p]
                if len(parts) == 4 and parts[2].isdigit():
                    self._handle_goal_message(int(parts[2]), body)
                else:
                    json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            elif path == "/api/checkpoint":
                self._handle_checkpoint(body)
            elif path == "/api/checkpoint/restore":
                # Gated: never silent restore — operator must confirm offline.
                json_response(self, HTTPStatus.OK, {
                    "ok": False,
                    "error": "checkpoint_restore_requires_operator_confirm",
                    "goal_id": body.get("goal_id"),
                })
            elif path == "/api/pr":
                self._handle_pr_gated(body)
            else:
                json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
        except ValueError as exc:
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except LookupError as exc:
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
        except Exception as exc:
            json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def _handle_health(self) -> None:
        path = runtime_state_path()
        age = None
        if path and path.is_file():
            age = round(time.time() - path.stat().st_mtime, 2)
        json_response(self, HTTPStatus.OK, {
            "ok": True,
            "heartbeat_age_s": age,
            "runtime_state": str(path) if path else None,
            "time": now_iso(),
        })

    def _handle_status(self) -> None:
        db = db_connect()
        try:
            state = autonomy_state()
            runtime = read_runtime_state() or {}
            goals = state.list_autonomous_goals(db)
            ctl = state.effective_control(db)
            heartbeat = None
            rpath = runtime_state_path()
            if rpath and rpath.is_file():
                heartbeat = round(time.time() - rpath.stat().st_mtime, 2)
            json_response(self, HTTPStatus.OK, {
                "ok": True,
                "runtime": runtime,
                "guardian": guardian_summary(db),
                "autonomous_goals": goals,
                "control": ctl,
                "current_tasks": current_tasks(db),
                "heartbeat_age_s": heartbeat,
                "config": {
                    "autonomy": autonomy_controller().load_config(),
                    "auto_approval": approval_policy().load_autonomy_config(),
                },
            })
        finally:
            db.close()

    def _handle_goals_list(self) -> None:
        db = db_connect()
        try:
            state = autonomy_state()
            try:
                # Hide cancelled leftovers; Delete now purges rows entirely.
                rows = db.execute(
                    """SELECT id FROM goals
                       WHERE autonomous = 1
                         AND LOWER(COALESCE(status,'')) != 'cancelled'
                       ORDER BY id DESC LIMIT 200"""
                ).fetchall()
            except sqlite3.Error:
                rows = db.execute(
                    """SELECT id FROM goals
                       WHERE LOWER(COALESCE(status,'')) != 'cancelled'
                       ORDER BY id DESC LIMIT 200"""
                ).fetchall()
            goals = []
            for row in rows:
                gid = row[0] if not isinstance(row, sqlite3.Row) else row["id"]
                obj = state.get_objective(db, gid)
                if not obj:
                    continue
                ctl = state.effective_control(db, gid)
                ctl_inner = (ctl or {}).get("control") or {}
                ctl_state = (ctl or {}).get("state") or "run"
                ctl_reason = ctl_inner.get("reason")
                needs_help = ctl_state == "pause" or (
                    str(obj.get("status") or "") == "blocked"
                )
                # Slim fields for dropdown / table.
                goals.append({
                    "id": obj.get("id", gid),
                    "status": obj.get("status"),
                    "description": obj.get("description"),
                    "objective": obj.get("objective"),
                    "autonomous": obj.get("autonomous"),
                    "control": ctl_state,
                    "control_reason": ctl_reason,
                    "needs_help": needs_help,
                    "checklist_pending": sum(
                        1 for c in (obj.get("checklist") or [])
                        if (c.get("status") or "") == "pending"
                    ),
                })
            json_response(self, HTTPStatus.OK, {"ok": True, "goals": goals})
        finally:
            db.close()

    def _handle_goal_detail(self, goal_id: int) -> None:
        db = db_connect()
        try:
            state = autonomy_state()
            obj = state.get_objective(db, goal_id)
            if obj is None:
                json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "goal not found"})
                return
            obj["control"] = state.effective_control(db, goal_id)
            ctl = obj["control"] or {}
            ctl_inner = ctl.get("control") or {}
            obj["needs_help"] = (
                ctl.get("state") == "pause"
                or str(obj.get("status") or "") == "blocked"
            )
            obj["help_message"] = ctl_inner.get("reason") if obj["needs_help"] else None
            obj["latest_checkpoint"] = state.latest_checkpoint(db, goal_id)
            obj["progress"] = progress_excerpt_for_goal(db, goal_id)
            obj["current_tasks"] = current_tasks(db, goal_id)
            proj = project_for_goal(db, goal_id)
            if proj:
                obj["project_key"] = proj.get("project_key")
                obj["workspace"] = proj.get("workspace")
                obj["repository"] = proj.get("repository")
            json_response(self, HTTPStatus.OK, {"ok": True, "goal": obj})
        finally:
            db.close()

    def _handle_goal_progress(self, goal_id: int) -> None:
        db = db_connect()
        try:
            payload = progress_excerpt_for_goal(db, goal_id)
            code = HTTPStatus.OK if payload.get("ok") else HTTPStatus.OK
            json_response(self, code, payload)
        finally:
            db.close()

    def _handle_control(self, body: dict) -> None:
        action = str(body.get("action") or "").lower()
        if action not in ("pause", "resume", "stop", "delete"):
            json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "action must be pause|resume|stop|delete"},
            )
            return
        goal_id = body.get("goal_id")
        if goal_id is not None:
            try:
                goal_id = int(goal_id)
            except (TypeError, ValueError):
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "goal_id must be int"})
                return
        # Per-goal actions require a selected goal (delete always; pause/resume preferred).
        if action == "delete" and goal_id is None:
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "goal_id required to delete"})
            return
        reason = body.get("reason") or f"dashboard_{action}"
        by_who = body.get("by") or "dashboard"
        state_mod = autonomy_state()
        goals_mod = None
        try:
            goals_mod = load_mod("anu-goal-service-v0.1.py", "anu_goals_dash_ctl")
        except Exception:
            goals_mod = None
        db = db_connect()
        try:
            scope = "goal" if goal_id else "global"
            help_message = None
            project_key = None
            if goal_id:
                try:
                    proj = project_for_goal(db, goal_id)
                    project_key = (proj or {}).get("project_key")
                except Exception:
                    project_key = None

            if action == "resume":
                state_mod.set_control(
                    db, "run", scope=scope, goal_id=goal_id,
                    reason=reason or "operator_resume", by_who=by_who,
                )
                if goal_id and goals_mod is not None:
                    try:
                        cur = goals_mod.get_goal(db, goal_id)
                        if cur and cur.get("status") == "blocked":
                            goals_mod.update_goal_status(
                                db, goal_id, "active", reason="operator_resume",
                            )
                    except Exception:
                        pass
                if goal_id:
                    state_mod.emit_autonomy_event(
                        db, goal_id, "AUTONOMY_CONTINUE",
                        "operator resume", {"by": by_who},
                    )
                    if project_key:
                        try:
                            state_mod.write_progress_file(
                                db, goal_id, project_key,
                                current_task="RESUMED",
                                blockers=[],
                            )
                        except Exception:
                            pass

            elif action == "delete":
                help_message = "Goal deleted by operator (work + memory purged)."
                # Stop control first so the runtime drops the goal immediately.
                try:
                    state_mod.set_control(
                        db, "stop", scope="goal", goal_id=goal_id,
                        reason=help_message, by_who=by_who,
                    )
                except Exception:
                    pass
                purge = None
                if goals_mod is not None and hasattr(goals_mod, "purge_goal"):
                    purge = goals_mod.purge_goal(db, goal_id, by_who=by_who)
                else:
                    # Fallback: at least cancel if purge helper missing.
                    try:
                        if goals_mod is not None:
                            goals_mod.update_goal_status(
                                db, goal_id, "cancelled",
                                reason=help_message, force=True,
                            )
                        else:
                            db.execute(
                                "UPDATE goals SET status = ? WHERE id = ?",
                                ("cancelled", goal_id),
                            )
                            db.commit()
                        purge = {"ok": True, "deleted": False, "cancelled": True}
                    except Exception as exc:
                        purge = {"ok": False, "error": str(exc)}
                if project_key and purge and purge.get("ok"):
                    try:
                        progress = (
                            BASE / "workspace" / "projects" / str(project_key)
                            / "AUTONOMY_PROGRESS.md"
                        )
                        if progress.is_file():
                            text = progress.read_text(encoding="utf-8", errors="replace")
                            marker = f"goal {goal_id}"
                            if marker in text.lower() or f"goal-{goal_id}" in text.lower():
                                progress.write_text(
                                    f"# AUTONOMY_PROGRESS\n\n"
                                    f"Goal #{goal_id} deleted by {by_who}.\n",
                                    encoding="utf-8",
                                )
                    except Exception:
                        pass
                payload = {
                    "ok": bool(purge and purge.get("ok")),
                    "action": "delete",
                    "goal_id": goal_id,
                    "help_message": help_message,
                    "purge": purge,
                    "control": {"state": "stop"},
                }
                if not payload["ok"]:
                    json_response(
                        self, HTTPStatus.INTERNAL_SERVER_ERROR, payload,
                    )
                    return
                json_response(self, HTTPStatus.OK, payload)
                return

            else:
                mapped = "pause" if action == "pause" else "stop"
                if action == "pause":
                    help_message = (
                        body.get("help_message")
                        or "Paused by operator — progress and checkpoint saved. "
                           "Resume when ready, or Delete to cancel this goal."
                    )
                    reason = help_message
                state_mod.set_control(
                    db, mapped, scope=scope, goal_id=goal_id,
                    reason=reason, by_who=by_who,
                )
                if goal_id:
                    state_mod.emit_autonomy_event(
                        db, goal_id, f"AUTONOMY_{mapped.upper()}",
                        reason, {"by": by_who, "needs_help": action == "pause"},
                    )
                    if action == "pause" and goals_mod is not None:
                        try:
                            cur = goals_mod.get_goal(db, goal_id)
                            if cur and cur.get("status") == "active":
                                goals_mod.update_goal_status(
                                    db, goal_id, "blocked", reason=help_message,
                                )
                        except Exception:
                            pass
                    if action == "pause":
                        try:
                            state_mod.save_checkpoint(
                                db, goal_id,
                                {
                                    "action": "pause",
                                    "by": by_who,
                                    "help_message": help_message,
                                    "at": now_iso(),
                                },
                            )
                        except Exception:
                            pass
                    if project_key:
                        try:
                            state_mod.write_progress_file(
                                db, goal_id, project_key,
                                current_task="PAUSED" if action == "pause" else "STOPPED",
                                blockers=[help_message or reason],
                            )
                        except Exception:
                            pass

            ctl = state_mod.effective_control(db, goal_id)
            payload = {
                "ok": True,
                "control": ctl,
                "goal_id": goal_id,
                "action": action,
            }
            if help_message:
                payload["help_message"] = help_message
                payload["needs_help"] = action in ("pause", "delete")
            if goal_id:
                try:
                    g = state_mod.get_objective(db, goal_id)
                    if g:
                        payload["goal_status"] = g.get("status")
                except Exception:
                    pass
            json_response(self, HTTPStatus.OK, payload)
        finally:
            db.close()

    def _handle_approval(self, approval_id: int, body: dict) -> None:
        action = str(body.get("action") or "").lower()
        if action not in ("approve", "deny"):
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "action must be approve|deny"})
            return
        by = body.get("by") or "dashboard"
        note = body.get("note")
        svc = approval_service()
        db = db_connect()
        try:
            if action == "approve":
                rec = svc.approve(db, approval_id, decided_by=by, note=note)
            else:
                rec = svc.reject(db, approval_id, decided_by=by, note=note)
            json_response(self, HTTPStatus.OK, {"ok": True, "approval": rec})
        finally:
            db.close()

    def _handle_pr_gated(self, body: dict) -> None:
        """Never auto-merge. Returns gated payload for human/token create."""
        goal_id = body.get("goal_id")
        if goal_id is None:
            raise ValueError("goal_id required")
        db = db_connect()
        try:
            git = load_mod("anu-git-workflow-v0.1.py", "anu_git_dash_pr")
            info = git_info_for_goal(db, int(goal_id))
            matrix = matrix_for_goal(db, int(goal_id))
            out = git.create_pr_gated(
                approved=False,
                title=f"ANU goal {goal_id}",
                head_branch=info.get("branch") or f"agent/{goal_id}-work",
                matrix_evidence=matrix,
                goal_id=int(goal_id),
            )
            json_response(self, HTTPStatus.OK, out)
        finally:
            db.close()

    def _handle_create_goal(self, body: dict) -> None:
        project_key = str(body.get("project_key") or "").strip()
        description = str(body.get("description") or "").strip()
        github = str(body.get("github") or "").strip() or None
        if not description:
            raise ValueError("description required")
        if not project_key and not github:
            raise ValueError("project_key or github required")
        objective = str(body.get("objective") or description).strip()
        criteria = body.get("criteria")
        autonomous = bool(body.get("autonomous", True))
        clone = bool(body.get("clone", True))
        db = db_connect()
        try:
            if github:
                # Reject non-allowlisted owners via assign module.
                assign = load_mod("anu-project-assign-v0.1.py", "anu_assign_dash")
                crit = criteria
                if isinstance(crit, str):
                    crit = [x.strip() for x in crit.split(",") if x.strip()]
                result = assign.connect_project(
                    db,
                    description=description,
                    project_key=project_key or None,
                    github=github,
                    clone=clone,
                    # Always expand autonomous lab goals into research→…→verify.
                    expand=bool(autonomous),
                    objective=objective,
                    criteria=crit,
                    autonomous=autonomous,
                )
                goal = ((result.get("assign") or {}).get("goal")
                        or (result.get("assign") or {}).get("autonomy"))
                json_response(self, HTTPStatus.CREATED, {"ok": True, "goal": goal, "result": result})
                return
            project_id = resolve_project_id(db, project_key)
            cur = db.execute(
                "INSERT INTO goals(project_id, description, status) VALUES (?, ?, 'pending')",
                (project_id, description),
            )
            goal_id = int(cur.lastrowid)
            db.commit()
            state = autonomy_state()
            goal = state.create_objective(
                db, goal_id, objective, criteria=criteria, autonomous=autonomous,
            )
            json_response(self, HTTPStatus.CREATED, {"ok": True, "goal": goal})
        finally:
            db.close()

    def _handle_goal_message(self, goal_id: int, body: dict) -> None:
        message = str(body.get("message") or "").strip()
        if not message:
            raise ValueError("message required")
        db = db_connect()
        try:
            row = db.execute("SELECT id FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if row is None:
                raise LookupError("goal not found")
            router = llm_router()
            router.store_context(db, goal_id=goal_id, task_id=None, role="user", content=message)
            state = autonomy_state()
            state.emit_autonomy_event(
                db, goal_id, "AUTONOMY_CONTINUE",
                "operator message via dashboard",
                {"message": message[:500]},
            )
            json_response(self, HTTPStatus.OK, {"ok": True})
        finally:
            db.close()

    def _handle_checkpoint(self, body: dict) -> None:
        goal_id = body.get("goal_id")
        if goal_id is None:
            raise ValueError("goal_id required")
        goal_id = int(goal_id)
        db = db_connect()
        try:
            snap = build_checkpoint_snapshot(db, goal_id)
            head = git_info_for_goal(db, goal_id).get("head")
            state = autonomy_state()
            cid = state.save_checkpoint(db, goal_id, snap, branch_head=head)
            json_response(self, HTTPStatus.OK, {"ok": True, "checkpoint_id": cid})
        finally:
            db.close()


def make_handler(token: str | None):
    class Handler(DashboardHandler):
        expected_token = token

    return Handler


def cmd_serve(args: argparse.Namespace) -> int:
    token = read_dashboard_token()
    if not token:
        print("ANU_DASHBOARD_TOKEN or ANU_DASHBOARD_TOKEN_FILE is required", file=sys.stderr)
        return 2
    host = args.host
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("Refusing to bind outside localhost; use --host 127.0.0.1", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((host, args.port), make_handler(token))
    print(f"ANU dashboard listening on http://{host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ANU Autonomy Dashboard v0.1")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="Run HTTP dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    if args.cmd == "serve":
        return cmd_serve(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
