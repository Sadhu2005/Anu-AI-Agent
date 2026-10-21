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
            tail = data.splitlines()[-n:]
            return redact_log_text("\n".join(tail))
        except OSError:
            continue
    return ""


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
        return {
            "ok": branch.returncode == 0,
            "project_key": key,
            "path": str(root),
            "branch": (branch.stdout or "").strip() if branch.returncode == 0 else None,
            "head": (head.stdout or "").strip() if head.returncode == 0 else None,
            "log": (log.stdout or "").strip().splitlines(),
            "status": (status.stdout or "").strip().splitlines(),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc), "project_key": key}


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
<style>
:root { --bg:#0f1419; --panel:#1a2332; --text:#e7ecf3; --muted:#8b9bb4; --accent:#3d8bfd; --warn:#f0ad4e; --bad:#e55353; --ok:#3ddc97; }
* { box-sizing: border-box; }
body { margin:0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background:var(--bg); color:var(--text); line-height:1.4; }
header { padding:1rem; background:var(--panel); border-bottom:1px solid #243044; position:sticky; top:0; z-index:2; }
h1 { margin:0 0 .25rem; font-size:1.25rem; }
.meta { color:var(--muted); font-size:.85rem; }
.grid { display:grid; gap:.75rem; padding:.75rem; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
.card { background:var(--panel); border:1px solid #243044; border-radius:8px; padding:.75rem; }
.card h2 { margin:0 0 .5rem; font-size:1rem; }
.gauge { display:flex; justify-content:space-between; font-size:.9rem; margin:.25rem 0; }
.bar { height:6px; background:#243044; border-radius:4px; overflow:hidden; margin-top:2px; }
.bar > i { display:block; height:100%; background:var(--accent); width:0%; }
.row { display:flex; flex-wrap:wrap; gap:.5rem; align-items:center; }
button, input, textarea, select { font:inherit; border-radius:6px; border:1px solid #3a4a63; background:#121a26; color:var(--text); padding:.4rem .6rem; }
button { cursor:pointer; background:#243044; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button.danger { background:var(--bad); border-color:var(--bad); }
pre, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.78rem; white-space:pre-wrap; word-break:break-word; max-height:220px; overflow:auto; background:#0b1018; padding:.5rem; border-radius:6px; }
.list { max-height:240px; overflow:auto; }
.item { padding:.35rem 0; border-bottom:1px solid #243044; font-size:.85rem; }
.banner { background:#3a2a12; color:#ffdca8; padding:.5rem .75rem; margin:.75rem; border-radius:6px; display:none; }
label { font-size:.8rem; color:var(--muted); display:block; margin-bottom:.15rem; }
form .field { margin-bottom:.5rem; }
@media (max-width:640px) { header { position:static; } }
</style>
</head>
<body>
<header>
  <h1>ANU Autonomy Dashboard</h1>
  <div class="meta" id="hdr-meta">Connecting…</div>
  <div class="row" style="margin-top:.5rem">
    <button type="button" id="btn-pause">Pause</button>
    <button type="button" id="btn-resume">Resume</button>
    <button type="button" class="danger" id="btn-stop">Stop</button>
    <label style="margin-left:auto"><input type="number" id="goal-filter" placeholder="goal id" style="width:6rem"/></label>
  </div>
</header>
<div class="banner" id="blocker-banner"></div>
<main class="grid">
  <section class="card"><h2>System</h2><div id="system-panel"></div></section>
  <section class="card"><h2>Current task</h2><div id="task-panel" class="list"></div></section>
  <section class="card"><h2>Checklist</h2><div id="checklist-panel" class="list"></div></section>
  <section class="card"><h2>Events</h2><div id="events-panel" class="list"></div></section>
  <section class="card"><h2>Logs</h2><pre id="logs-panel"></pre></section>
  <section class="card"><h2>Tests</h2><div id="tests-panel" class="list"></div></section>
  <section class="card"><h2>Git</h2><pre id="git-panel"></pre></section>
  <section class="card"><h2>LLM</h2><div id="llm-panel" class="list"></div></section>
  <section class="card"><h2>Approvals</h2><div id="approvals-panel" class="list"></div></section>
  <section class="card"><h2>New goal</h2>
    <form id="goal-form">
      <div class="field"><label>project_key</label><input name="project_key" required/></div>
      <div class="field"><label>description</label><input name="description" required/></div>
      <div class="field"><label>objective</label><input name="objective"/></div>
      <div class="field"><label><input type="checkbox" name="autonomous" checked/> autonomous</label></div>
      <button type="submit" class="primary">Create goal</button>
    </form>
  </section>
  <section class="card"><h2>Message goal</h2>
    <form id="msg-form">
      <div class="field"><label>goal_id</label><input name="goal_id" type="number" required/></div>
      <div class="field"><label>message</label><textarea name="message" rows="3" required></textarea></div>
      <button type="submit" class="primary">Send</button>
    </form>
  </section>
</main>
<script>
(function(){
  const TOKEN_KEY = "anu_dashboard_token";
  function token(){
    let t = sessionStorage.getItem(TOKEN_KEY);
    if(!t){ t = prompt("ANU dashboard token:"); if(t) sessionStorage.setItem(TOKEN_KEY, t); }
    return t || "";
  }
  function el(id){ return document.getElementById(id); }
  function setText(node, text){ node.textContent = text == null ? "" : String(text); }
  function clear(node){ while(node.firstChild) node.removeChild(node.firstChild); }
  function addLine(parent, text){
    const d = document.createElement("div"); d.className = "item"; setText(d, text); parent.appendChild(d);
  }
  function gauge(parent, label, value, pct){
    const row = document.createElement("div"); row.className = "gauge";
    const l = document.createElement("span"); setText(l, label);
    const v = document.createElement("span"); setText(v, value);
    row.appendChild(l); row.appendChild(v); parent.appendChild(row);
    if(pct != null){
      const bar = document.createElement("div"); bar.className = "bar";
      const i = document.createElement("i"); i.style.width = Math.max(0, Math.min(100, pct)) + "%";
      bar.appendChild(i); parent.appendChild(bar);
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
  async function refreshStatus(){
    const st = await api("/api/status");
    const ctl = st.control || {};
    setText(el("hdr-meta"), "control=" + (ctl.state||"run") + " | goals=" + (st.autonomous_goals||[]).length + " | heartbeat=" + (st.heartbeat_age_s != null ? st.heartbeat_age_s + "s" : "n/a"));
    const tasks = el("task-panel"); clear(tasks);
    (st.current_tasks||[]).forEach(t=> addLine(tasks, "#"+t.id+" "+t.title+" ["+t.status+"]"));
    const goalId = selectedGoal() || (st.autonomous_goals && st.autonomous_goals[0] && st.autonomous_goals[0].id);
    if(goalId){
      const g = await api("/api/goals/"+goalId);
      const cl = el("checklist-panel"); clear(cl);
      (g.checklist||[]).forEach(c=> addLine(cl, "["+c.status+"] "+c.item));
      const blockers = (g.checklist||[]).filter(c=>c.status==="blocked").map(c=>c.item);
      const banner = el("blocker-banner");
      if(blockers.length){ banner.style.display="block"; setText(banner, "Blockers: "+blockers.join("; ")); }
      else { banner.style.display="none"; }
    }
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
    if(s.loadavg) gauge(p, "Load", s.loadavg["1m"]+" / "+s.loadavg["5m"]+" / "+s.loadavg["15m"], null);
    if(s.temperature_c != null) gauge(p, "Temp °C", s.temperature_c, null);
    if(s.uptime_seconds != null) gauge(p, "Uptime s", s.uptime_seconds, null);
  }
  async function refreshEvents(){
    const ev = await api("/api/events?limit=40");
    const p = el("events-panel"); clear(p);
    (ev.events||[]).forEach(e=> addLine(p, e.created_at+" "+e.event_type+": "+(e.message||"")));
  }
  async function refreshLogs(){
    const lg = await api("/api/logs?tail=120");
    setText(el("logs-panel"), lg.text||"");
  }
  async function refreshPanels(){
    const gid = selectedGoal();
    const q = gid ? "?goal="+gid : "";
    const tests = await api("/api/tests"+q);
    const tp = el("tests-panel"); clear(tp);
    (tests.events||[]).forEach(e=> addLine(tp, e.created_at+" "+e.event_type+": "+(e.message||"")));
    const git = await api("/api/git"+q);
    setText(el("git-panel"), git.ok ? [(git.branch||"")+" @ "+(git.head||""), ...(git.log||[]), ...(git.status||[])].join("\n") : (git.error||"n/a"));
    const llm = await api("/api/llm");
    const lp = el("llm-panel"); clear(lp);
    (llm.providers||[]).forEach(pr=> addLine(lp, pr.provider+": "+(pr.available?"ok":"down")+" — "+(pr.why||"")));
    const appr = await api("/api/approvals");
    const ap = el("approvals-panel"); clear(ap);
    (appr.approvals||[]).forEach(a=>{
      const row = document.createElement("div"); row.className="item";
      setText(row, "#"+a.id+" "+a.action+" task="+a.task_id+" — "+a.reason);
      const yes = document.createElement("button"); setText(yes,"Approve"); yes.onclick=()=>decide(a.id,"approve");
      const no = document.createElement("button"); setText(no,"Deny"); no.onclick=()=>decide(a.id,"deny");
      row.appendChild(yes); row.appendChild(no); ap.appendChild(row);
    });
  }
  async function decide(id, action){
    await api("/api/approvals/"+id, {method:"POST", body: JSON.stringify({action})});
    refreshPanels();
  }
  async function control(action){
    if(action==="stop" && !confirm("Stop autonomy?")) return;
    const gid = selectedGoal();
    await api("/api/control", {method:"POST", body: JSON.stringify({action, goal_id: gid||undefined})});
    refreshStatus();
  }
  el("btn-pause").onclick=()=>control("pause");
  el("btn-resume").onclick=()=>control("resume");
  el("btn-stop").onclick=()=>control("stop");
  el("goal-form").onsubmit=async (e)=>{
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      project_key: fd.get("project_key"),
      description: fd.get("description"),
      objective: fd.get("objective") || undefined,
      autonomous: !!fd.get("autonomous")
    };
    const res = await api("/api/goals", {method:"POST", body: JSON.stringify(body)});
    alert("Created goal "+res.goal.id);
    refreshStatus();
  };
  el("msg-form").onsubmit=async (e)=>{
    e.preventDefault();
    const fd = new FormData(e.target);
    const gid = fd.get("goal_id");
    await api("/api/goals/"+gid+"/message", {method:"POST", body: JSON.stringify({message: fd.get("message")})});
    e.target.reset();
  };
  async function tick(){
    try{
      await Promise.all([refreshStatus(), refreshSystem(), refreshEvents(), refreshLogs(), refreshPanels()]);
    }catch(err){ setText(el("hdr-meta"), "Error: "+err.message); }
  }
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
            elif path == "/api/approvals":
                db = db_connect()
                try:
                    svc = approval_service()
                    json_response(self, HTTPStatus.OK, {"ok": True, "approvals": svc.list_pending(db)})
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
                parts = path.split("/")
                if len(parts) == 4 and parts[2].isdigit():
                    self._handle_goal_message(int(parts[2]), body)
                else:
                    json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            elif path == "/api/checkpoint":
                self._handle_checkpoint(body)
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
            rows = db.execute(
                """SELECT id FROM goals WHERE autonomous = 1 ORDER BY id DESC LIMIT 200"""
            ).fetchall()
            goals = []
            for row in rows:
                gid = row[0] if not isinstance(row, sqlite3.Row) else row["id"]
                obj = state.get_objective(db, gid)
                if obj:
                    goals.append(obj)
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
            obj["latest_checkpoint"] = state.latest_checkpoint(db, goal_id)
            json_response(self, HTTPStatus.OK, {"ok": True, "goal": obj})
        finally:
            db.close()

    def _handle_control(self, body: dict) -> None:
        action = str(body.get("action") or "").lower()
        if action not in ("pause", "resume", "stop"):
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "action must be pause|resume|stop"})
            return
        goal_id = body.get("goal_id")
        reason = body.get("reason") or f"dashboard {action}"
        by_who = body.get("by") or "dashboard"
        state_mod = autonomy_state()
        db = db_connect()
        try:
            if action == "resume":
                scope = "goal" if goal_id else "global"
                state_mod.set_control(db, "run", scope=scope, goal_id=goal_id, reason=reason, by_who=by_who)
            else:
                mapped = "pause" if action == "pause" else "stop"
                scope = "goal" if goal_id else "global"
                state_mod.set_control(db, mapped, scope=scope, goal_id=goal_id, reason=reason, by_who=by_who)
                if goal_id:
                    state_mod.emit_autonomy_event(
                        db, int(goal_id), f"AUTONOMY_{mapped.upper()}",
                        reason, {"by": by_who},
                    )
            json_response(self, HTTPStatus.OK, {
                "ok": True,
                "control": state_mod.effective_control(db, goal_id),
            })
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

    def _handle_create_goal(self, body: dict) -> None:
        project_key = str(body.get("project_key") or "").strip()
        description = str(body.get("description") or "").strip()
        if not project_key or not description:
            raise ValueError("project_key and description required")
        objective = str(body.get("objective") or description).strip()
        criteria = body.get("criteria")
        autonomous = bool(body.get("autonomous", True))
        db = db_connect()
        try:
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
