#!/usr/bin/env python3
"""ANU Autonomy Controller v0.1 (V0.9.0) — owns CONTINUE / REPLAN / PAUSE / COMPLETE.

The controller decides what happens after each scheduler cycle for autonomous
goals. It never claims success without evidence and stops only on real
boundaries or configured budgets.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"
DEFAULT_CONFIG = BASE / "config" / "anu-autonomy.yaml"

DECIDE_CONTINUE = "CONTINUE"
DECIDE_REPLAN = "REPLAN"
DECIDE_PAUSE = "PAUSE_FOR_USER"
DECIDE_COMPLETE = "COMPLETE"
DECIDE_WAIT = "WAIT"  # temporary (guardian / cooldown)


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


def _state():
    return _load("anu-autonomy-state-v0.1.py", "anu_autonomy_state_v090")


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML required for anu-autonomy.yaml") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    return data


def load_config(path: Path | None = None) -> dict:
    candidates = []
    if path is not None:
        candidates.append(Path(path))
    if os.environ.get("ANU_AUTONOMY_CONFIG"):
        candidates.append(Path(os.environ["ANU_AUTONOMY_CONFIG"]))
    candidates.extend([
        BASE / "config" / "anu-autonomy.yaml",
        _REPO / "config" / "anu-autonomy.yaml",
    ])
    chosen = next((c for c in candidates if c.is_file()), None)
    defaults = {
        "enabled": True,
        "auto_approve": True,
        "limits": {
            "max_wall_hours": 8,
            "max_iterations": 200,
            "max_retries_per_task": 3,
            "max_same_fingerprint": 3,
            "max_replans": 10,
            "no_progress_cycles": 5,
            "max_fix_attempts_per_stage": 5,
        },
        "scheduler": {
            "max_tasks_per_iteration": 3,
            "interval_seconds": 15,
        },
        "guardian": {"treat_as_wait": True},
        "progress_file": "AUTONOMY_PROGRESS.md",
    }
    if chosen is None:
        return defaults
    raw = _load_yaml(chosen)
    merged = {**defaults, **raw}
    merged["limits"] = {**defaults["limits"], **(raw.get("limits") or {})}
    merged["scheduler"] = {**defaults["scheduler"], **(raw.get("scheduler") or {})}
    merged["_config_path"] = str(chosen)
    return merged


def db_connect(db_path=None):
    return _state().db_connect(db_path if db_path is not None else DB_FILE)


def fingerprint_failure(operation, error_class, message):
    """Stable fingerprint for loop-guard (op + class + normalized message)."""
    msg = re.sub(r"\d+", "N", str(message or "").lower())
    msg = re.sub(r"\s+", " ", msg).strip()[:240]
    raw = f"{operation}|{error_class}|{msg}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _task_rows_for_goal(db, goal_id):
    rows = db.execute(
        """SELECT t.id, t.status, t.attempts, t.title,
                  COALESCE(s.operation, '') AS operation
           FROM tasks t
           LEFT JOIN task_execution_specs s ON s.task_id = t.id
           WHERE t.goal_id = ?
           ORDER BY t.id""",
        (int(goal_id),),
    ).fetchall()
    out = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            out.append(dict(row))
        else:
            out.append({
                "id": row[0],
                "status": row[1],
                "attempts": row[2],
                "title": row[3],
                "operation": row[4],
            })
    return out


def _ready_tasks(db, goal_id):
    """Tasks that are pending/ready with deps satisfied (best-effort)."""
    tasks = _task_rows_for_goal(db, goal_id)
    ready = []
    for task in tasks:
        if str(task.get("status") or "").lower() not in ("pending", "ready", "waiting"):
            if str(task.get("status") or "").lower() == "queued":
                pass
            else:
                continue
        status = str(task.get("status") or "").lower()
        if status == "waiting":
            # Waiting on approval — still "ready" for policy to decide.
            ready.append(task)
            continue
        tid = task["id"]
        try:
            deps = db.execute(
                """SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?""",
                (tid,),
            ).fetchall()
        except sqlite3.OperationalError:
            deps = []
        ok = True
        for dep in deps:
            dep_id = dep[0] if not isinstance(dep, sqlite3.Row) else dep["depends_on_task_id"]
            dep_row = db.execute(
                "SELECT status FROM tasks WHERE id = ?", (dep_id,)
            ).fetchone()
            if dep_row is None:
                ok = False
                break
            dep_status = (dep_row[0] if not isinstance(dep_row, sqlite3.Row) else dep_row["status"] or "").lower()
            if dep_status not in (
                "completed", "complete", "committed", "done", "skipped"
            ):
                ok = False
                break
        if ok and status in ("pending", "ready", "queued"):
            ready.append(task)
    return ready


def _all_complete(tasks):
    if not tasks:
        return False
    terminal_ok = {"completed", "complete", "committed", "done", "skipped"}
    for task in tasks:
        if str(task.get("status") or "").lower() not in terminal_ok:
            return False
    return True


def _has_failed(tasks):
    """Only count failures that still block the goal (ignore superseded blocks)."""
    failed = []
    for task in tasks:
        status = str(task.get("status") or "").lower()
        if status in ("failed", "error", "rolled_back", "blocked", "rejected"):
            failed.append(task)
    return failed


def _active_run(db, goal_id):
    row = db.execute(
        """SELECT id, goal_id, started_at, iterations, elapsed_seconds, status
           FROM autonomy_runs
           WHERE goal_id = ? AND status = 'running'
           ORDER BY id DESC LIMIT 1""",
        (int(goal_id),),
    ).fetchone()
    if row is None:
        return None
    return dict(row) if isinstance(row, sqlite3.Row) else {
        "id": row[0],
        "goal_id": row[1],
        "started_at": row[2],
        "iterations": row[3],
        "elapsed_seconds": row[4],
        "status": row[5],
    }


def _parse_started(started_at):
    if not started_at:
        return None
    try:
        return datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except ValueError:
        return None


def decide(db, goal_id, *, config=None, guardian_data=None, last_failure=None):
    """Return one of CONTINUE / REPLAN / PAUSE_FOR_USER / COMPLETE / WAIT."""
    state = _state()
    cfg = config or load_config()
    limits = cfg.get("limits") or {}
    ctl = state.effective_control(db, goal_id)
    if ctl["state"] == "stop":
        return {
            "decision": DECIDE_PAUSE,
            "reason": "agent_control_stop",
            "detail": ctl.get("control"),
        }
    if ctl["state"] == "pause":
        return {
            "decision": DECIDE_PAUSE,
            "reason": "agent_control_pause",
            "detail": ctl.get("control"),
        }

    if guardian_data and isinstance(guardian_data, dict):
        gstatus = str(guardian_data.get("status") or "").upper()
        if gstatus in ("PAUSE", "THROTTLE", "CRITICAL") and cfg.get("guardian", {}).get("treat_as_wait", True):
            return {
                "decision": DECIDE_WAIT,
                "reason": f"guardian_{gstatus.lower()}",
                "detail": guardian_data,
            }

    obj = state.get_objective(db, goal_id)
    if obj is None:
        return {"decision": DECIDE_PAUSE, "reason": "goal_not_found"}
    if not obj.get("autonomous"):
        return {"decision": DECIDE_PAUSE, "reason": "goal_not_autonomous"}

    run = _active_run(db, goal_id)
    iterations = int((run or {}).get("iterations") or 0)
    if iterations >= int(limits.get("max_iterations", 200)):
        return {
            "decision": DECIDE_PAUSE,
            "reason": "max_iterations",
            "detail": {"iterations": iterations},
        }

    started = _parse_started((run or {}).get("started_at"))
    if started is not None:
        hours = (datetime.now(timezone.utc) - started).total_seconds() / 3600.0
        if hours >= float(limits.get("max_wall_hours", 8)):
            return {
                "decision": DECIDE_PAUSE,
                "reason": "max_wall_hours",
                "detail": {"hours": hours},
            }

    tasks = _task_rows_for_goal(db, goal_id)
    if _all_complete(tasks):
        checklist = obj.get("checklist") or []
        pending = [c for c in checklist if c.get("status") == "pending"]
        # COMPLETE only when checklist done items have evidence or no checklist.
        if not pending:
            undone_without_evidence = [
                c for c in checklist
                if c.get("status") == "done" and not (c.get("evidence") or "").strip()
            ]
            if undone_without_evidence:
                return {
                    "decision": DECIDE_PAUSE,
                    "reason": "missing_evidence",
                    "detail": {"items": [c["id"] for c in undone_without_evidence]},
                }
            return {
                "decision": DECIDE_COMPLETE,
                "reason": "all_tasks_and_checklist_done",
                "detail": {"task_count": len(tasks)},
            }
        # Tasks done but checklist pending → COMPLETE; apply_decision stamps
        # evidence onto pending checklist items.
        if not checklist:
            return {
                "decision": DECIDE_COMPLETE,
                "reason": "all_tasks_done_no_checklist",
                "detail": {"task_count": len(tasks)},
            }
        return {
            "decision": DECIDE_COMPLETE,
            "reason": "all_tasks_done_checklist_pending",
            "detail": {
                "task_count": len(tasks),
                "pending_checklist": [c.get("id") for c in pending],
            },
        }

    ready = _ready_tasks(db, goal_id)
    if ready:
        return {
            "decision": DECIDE_CONTINUE,
            "reason": "ready_tasks",
            "detail": {"ready": [t["id"] for t in ready]},
        }

    failed = _has_failed(tasks)
    if failed:
        task = failed[-1]
        fp = fingerprint_failure(
            task.get("operation") or "unknown",
            (last_failure or {}).get("error_class") or task.get("status"),
            (last_failure or {}).get("message") or task.get("title") or "",
        )
        count = state.record_failure_fingerprint(
            db,
            goal_id,
            fp,
            task_id=task.get("id"),
            message=(last_failure or {}).get("message"),
        )
        if count >= int(limits.get("max_same_fingerprint", 3)):
            return {
                "decision": DECIDE_PAUSE,
                "reason": "same_fingerprint_limit",
                "detail": {"fingerprint": fp, "count": count, "task_id": task.get("id")},
            }
        # Cap replans via autonomy event count.
        replan_count = db.execute(
            """SELECT COUNT(*) FROM agent_events
               WHERE goal_id = ? AND event_type = 'AUTONOMY_REPLAN'""",
            (int(goal_id),),
        ).fetchone()[0]
        if int(replan_count) >= int(limits.get("max_replans", 10)):
            return {
                "decision": DECIDE_PAUSE,
                "reason": "max_replans",
                "detail": {"replans": replan_count},
            }
        return {
            "decision": DECIDE_REPLAN,
            "reason": "task_failed",
            "detail": {"task_id": task.get("id"), "fingerprint": fp, "count": count},
        }

    # No ready tasks, not all complete — likely waiting on deps or approval.
    waiting = [t for t in tasks if str(t.get("status") or "").lower() == "waiting"]
    if waiting:
        return {
            "decision": DECIDE_CONTINUE,
            "reason": "waiting_approval_or_wake",
            "detail": {"waiting": [t["id"] for t in waiting]},
        }

    # No-progress: identical pending set hashed across stored marker.
    pending_ids = tuple(
        t["id"] for t in tasks
        if str(t.get("status") or "").lower() in ("pending", "ready", "queued")
    )
    marker = hashlib.sha256(repr(pending_ids).encode()).hexdigest()[:16]
    prev = db.execute(
        """SELECT metadata FROM agent_events
           WHERE goal_id = ? AND event_type = 'AUTONOMY_CONTINUE'
           ORDER BY id DESC LIMIT ?""",
        (int(goal_id), int(limits.get("no_progress_cycles", 5))),
    ).fetchall()
    same = 0
    for row in prev:
        meta_raw = row[0] if not isinstance(row, sqlite3.Row) else row["metadata"]
        try:
            meta = json.loads(meta_raw or "{}")
        except json.JSONDecodeError:
            meta = {}
        if meta.get("progress_marker") == marker:
            same += 1
    if same >= int(limits.get("no_progress_cycles", 5)) - 1 and pending_ids:
        return {
            "decision": DECIDE_PAUSE,
            "reason": "no_progress_cycle",
            "detail": {"marker": marker, "pending": list(pending_ids)},
        }

    if not tasks:
        return {
            "decision": DECIDE_CONTINUE,
            "reason": "needs_expand",
            "detail": {},
        }

    return {
        "decision": DECIDE_CONTINUE,
        "reason": "idle_poll",
        "detail": {"progress_marker": marker},
    }


def _project_key_for_goal(db, goal_id):
    row = db.execute(
        """SELECT p.project_key FROM goals g
           JOIN projects p ON p.id = g.project_id
           WHERE g.id = ?""",
        (int(goal_id),),
    ).fetchone()
    if row is None:
        return None
    return row[0] if not isinstance(row, sqlite3.Row) else row["project_key"]


def _start_ready_tasks(db, goal_id, *, max_tasks=1):
    """Best-effort: wake first ready tasks via goal-cycle, else task engine."""
    started = []
    try:
        cycle = _load("anu-goal-cycle-v0.1.py", "anu_goal_cycle_v090")
    except FileNotFoundError:
        cycle = None
    if cycle is not None:
        for _ in range(max(1, int(max_tasks))):
            try:
                result = cycle.start_first_ready_for_goal(db, goal_id, max_tasks=1)
            except TypeError:
                result = cycle.start_first_ready_for_goal(db, goal_id)
            except Exception as exc:
                return {"started": started, "error": f"{type(exc).__name__}: {exc}"}
            if isinstance(result, dict) and result.get("started"):
                started.append(result)
            else:
                break
    if started:
        return {"started": started}
    # Fallback: scheduler discover may miss skipped-dep chains; start directly.
    ready = _ready_tasks(db, goal_id)
    if not ready:
        return {"started": started, "reason": "no_ready_task"}
    try:
        engine = _load("anu-task-engine-v0.5.3.py", "anu_task_engine_v090")
    except FileNotFoundError:
        return {"started": started, "reason": "task_engine_missing"}
    goal_row = db.execute(
        "SELECT description FROM goals WHERE id = ?",
        (int(goal_id),),
    ).fetchone()
    goal_text = ""
    if goal_row is not None:
        goal_text = (
            goal_row["description"]
            if isinstance(goal_row, sqlite3.Row)
            else goal_row[0]
        ) or ""
    try:
        gen = _load("anu-change-generator-v0.1.py", "anu_gen_ctrl_v090")
    except FileNotFoundError:
        gen = None

    for task in ready[: max(1, int(max_tasks))]:
        tid = task["id"]
        row = db.execute(
            """SELECT project, changes_file, operation
               FROM task_execution_specs WHERE task_id = ?""",
            (tid,),
        ).fetchone()
        if row is None:
            continue
        if isinstance(row, sqlite3.Row):
            project, changes_file, operation = (
                row["project"], row["changes_file"], row["operation"]
            )
        else:
            project, changes_file, operation = row[0], row[1], row[2]
        if gen is not None:
            try:
                title = task.get("title") if isinstance(task, dict) else None
                if not title:
                    trow = db.execute(
                        "SELECT title FROM tasks WHERE id = ?", (tid,)
                    ).fetchone()
                    if trow is not None:
                        title = (
                            trow["title"]
                            if isinstance(trow, sqlite3.Row)
                            else trow[0]
                        )
                role = gen.role_from_task_title(title)
                gen.prepare_for_task(
                    project, role, goal_text, filename=changes_file
                )
            except Exception:
                pass
        try:
            payload, code = engine.start_task(
                tid, project, changes_file, operation
            )
            started.append({
                "started": True,
                "goal_id": goal_id,
                "task_ids": [tid],
                "exit_code": code,
                "result": payload,
                "via": "task_engine_fallback",
            })
        except Exception as exc:
            started.append({
                "started": False,
                "task_id": tid,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return {"started": started}


def _insert_fix_tasks(db, goal_id, failure_detail):
    """Insert a linear fix task before remaining pending work (best-effort)."""
    try:
        planner = _load("anu-template-planner-v0.1.py", "anu_planner_v090")
    except FileNotFoundError:
        return {"ok": False, "reason": "planner_missing"}
    # Prefer dedicated helper when available.
    if hasattr(planner, "insert_fix_tasks_for_goal"):
        return planner.insert_fix_tasks_for_goal(db, goal_id, failure_detail)
    return {"ok": False, "reason": "insert_fix_tasks_unsupported"}


def _collect_complete_evidence(db, goal_id):
    """Build short evidence from completed tasks + last ACCEPT verification."""
    parts = [f"controller_complete:{now_iso()}"]
    try:
        rows = db.execute(
            """SELECT id, title, status FROM tasks
               WHERE goal_id = ? ORDER BY id""",
            (int(goal_id),),
        ).fetchall()
        done = []
        for row in rows:
            tid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
            title = row["title"] if isinstance(row, sqlite3.Row) else row[1]
            status = row["status"] if isinstance(row, sqlite3.Row) else row[2]
            done.append(f"{tid}:{status}:{title}")
        if done:
            parts.append("tasks=" + ";".join(done)[:800])
    except sqlite3.Error:
        pass
    try:
        vrow = db.execute(
            """SELECT id, decision, transaction_id FROM verification_records
               WHERE goal_id = ? AND decision = 'ACCEPT'
               ORDER BY id DESC LIMIT 1""",
            (int(goal_id),),
        ).fetchone()
        if vrow is not None:
            vid = vrow["id"] if isinstance(vrow, sqlite3.Row) else vrow[0]
            tx = (
                vrow["transaction_id"]
                if isinstance(vrow, sqlite3.Row)
                else vrow[2]
            )
            parts.append(f"verification_accept={vid}:tx={tx}")
    except sqlite3.Error:
        pass
    return " | ".join(parts)


def _mark_checklist_complete_with_evidence(db, goal_id, evidence=None):
    state = _state()
    blob = evidence or _collect_complete_evidence(db, goal_id)
    if hasattr(state, "complete_pending_checklist"):
        return state.complete_pending_checklist(db, goal_id, blob)
    # Fallback for older state module.
    items = state.list_checklist(db, goal_id)
    for item in items:
        if item.get("status") == "pending":
            try:
                state.update_checklist_item(
                    db, item["id"], status="done", evidence=blob
                )
            except ValueError:
                continue
    return {"ok": True, "checklist": items}


def apply_decision(db, goal_id, decision_result, *, config=None, project_key=None):
    """Execute the controller decision and update progress / events."""
    state = _state()
    cfg = config or load_config()
    decision = decision_result.get("decision")
    reason = decision_result.get("reason")
    detail = decision_result.get("detail") or {}
    project_key = project_key or _project_key_for_goal(db, goal_id)

    run = _active_run(db, goal_id)
    if run is None and decision in (DECIDE_CONTINUE, DECIDE_REPLAN):
        state.start_run(db, goal_id)
        run = _active_run(db, goal_id)

    if decision == DECIDE_WAIT:
        state.emit_autonomy_event(
            db, goal_id, "AUTONOMY_PAUSE", f"wait: {reason}", detail
        )
        if project_key:
            state.write_progress_file(
                db, goal_id, project_key,
                current_task=f"WAIT:{reason}",
                blockers=[reason],
            )
        return {"ok": True, "decision": decision, "reason": reason}

    if decision == DECIDE_PAUSE:
        state.set_control(
            db, "pause", scope="goal", goal_id=goal_id,
            reason=reason, by_who="controller",
        )
        state.emit_autonomy_event(
            db, goal_id, "AUTONOMY_PAUSE", f"pause: {reason}", detail
        )
        if run:
            state.end_run(db, run["id"], reason, status="paused")
        if project_key:
            state.write_progress_file(
                db, goal_id, project_key,
                current_task=None,
                blockers=[f"{reason}: {json.dumps(detail, default=str)[:200]}"],
            )
        return {"ok": True, "decision": decision, "reason": reason, "needs_user": True}

    if decision == DECIDE_COMPLETE:
        evidence = _collect_complete_evidence(db, goal_id)
        try:
            _mark_checklist_complete_with_evidence(db, goal_id, evidence)
        except Exception:
            # Never block COMPLETE on checklist bookkeeping; goal status still flips.
            try:
                db.execute(
                    """UPDATE goal_checklist
                       SET status = 'done', evidence = ?, updated_at = ?
                       WHERE goal_id = ? AND status = 'pending'""",
                    (evidence, now_iso(), int(goal_id)),
                )
                db.commit()
            except sqlite3.Error:
                pass
        state.emit_autonomy_event(
            db, goal_id, "AUTONOMY_COMPLETE", f"complete: {reason}", detail
        )
        if run:
            state.end_run(db, run["id"], reason, status="complete")
        # Mark goal completed if service allows.
        try:
            goals = _load("anu-goal-service-v0.1.py", "anu_goals_v090")
            goals.update_goal_status(db, goal_id, "completed")
        except Exception:
            try:
                db.execute(
                    "UPDATE goals SET status = ? WHERE id = ?",
                    ("completed", int(goal_id)),
                )
                db.commit()
            except sqlite3.Error:
                pass
        if project_key:
            state.write_progress_file(
                db, goal_id, project_key,
                current_task="COMPLETE",
                elapsed=(run or {}).get("elapsed_seconds"),
            )
        try:
            state.save_checkpoint(
                db, goal_id,
                {"decision": decision, "reason": reason, "at": now_iso()},
            )
        except Exception:
            pass
        return {"ok": True, "decision": decision, "reason": reason}

    if decision == DECIDE_REPLAN:
        # Attach DIAGNOSIS.md if present (matrix / test failures).
        try:
            proj = _project_key_for_goal(db, goal_id)
            if proj:
                diag_mod = _load("anu-failure-diagnose-v0.1.py", "anu_diag_ctrl")
                ws_row = db.execute(
                    """SELECT workspace FROM projects p
                       JOIN goals g ON g.project_id = p.id WHERE g.id = ?""",
                    (int(goal_id),),
                ).fetchone()
                ws = None
                if ws_row is not None:
                    ws = ws_row[0] if not isinstance(ws_row, sqlite3.Row) else ws_row["workspace"]
                if ws:
                    diagnosis = diag_mod.read_diagnosis(ws)
                    if diagnosis:
                        detail = {**detail, "diagnosis": diagnosis, "failure_class": diagnosis.get("primary_class")}
        except Exception:
            pass
        state.emit_autonomy_event(
            db, goal_id, "AUTONOMY_REPLAN", f"replan: {reason}", detail
        )
        fix = _insert_fix_tasks(db, goal_id, detail)
        if run:
            state.bump_run_iteration(db, run["id"])
        if project_key:
            state.write_progress_file(
                db, goal_id, project_key,
                current_task=f"REPLAN:{reason}",
            )
        return {"ok": True, "decision": decision, "reason": reason, "fix": fix}

    # CONTINUE
    meta = {"reason": reason, **detail}
    if "progress_marker" in detail:
        meta["progress_marker"] = detail["progress_marker"]
    state.emit_autonomy_event(
        db, goal_id, "AUTONOMY_CONTINUE", f"continue: {reason}", meta
    )
    max_tasks = int((cfg.get("scheduler") or {}).get("max_tasks_per_iteration", 1))
    started = _start_ready_tasks(db, goal_id, max_tasks=max_tasks)
    if run:
        state.bump_run_iteration(db, run["id"])
    if project_key:
        current = None
        if started.get("started"):
            current = started["started"][-1]
        state.write_progress_file(
            db, goal_id, project_key, current_task=current
        )
    try:
        state.save_checkpoint(
            db, goal_id,
            {"decision": decision, "reason": reason, "started": started, "at": now_iso()},
        )
    except Exception:
        pass
    return {"ok": True, "decision": decision, "reason": reason, "started": started}


def run_controller_once(db, *, config=None, guardian_data=None, goal_id=None):
    """Evaluate autonomous goals and apply decisions."""
    state = _state()
    cfg = config or load_config()
    if not cfg.get("enabled", True):
        return {"ok": True, "skipped": True, "reason": "autonomy_disabled"}
    state.migrate(db)
    results = []
    if goal_id is not None:
        goals = [state.get_objective(db, goal_id)]
        goals = [g for g in goals if g]
    else:
        goals = state.list_autonomous_goals(db)
    for goal in goals:
        if goal.get("status") in ("completed", "cancelled", "failed"):
            continue
        gid = goal["id"]
        decision = decide(
            db, gid, config=cfg, guardian_data=guardian_data
        )
        applied = apply_decision(db, gid, decision, config=cfg)
        results.append({"goal_id": gid, "decision": decision, "applied": applied})
    return {"ok": True, "results": results}


def active_autonomy_interval(config=None):
    cfg = config or load_config()
    return int((cfg.get("scheduler") or {}).get("interval_seconds", 15))


def active_autonomy_max_tasks(config=None):
    cfg = config or load_config()
    return int((cfg.get("scheduler") or {}).get("max_tasks_per_iteration", 3))


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Autonomy Controller v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    once = sub.add_parser("run-once")
    once.add_argument("--goal", type=int, default=None)
    pause = sub.add_parser("pause")
    pause.add_argument("--goal", type=int, default=None)
    pause.add_argument("--reason", default="operator_pause")
    resume = sub.add_parser("resume")
    resume.add_argument("--goal", type=int, default=None)
    stop = sub.add_parser("stop")
    stop.add_argument("--goal", type=int, default=None)
    stop.add_argument("--reason", default="operator_stop")
    decide_p = sub.add_parser("decide")
    decide_p.add_argument("--goal", type=int, required=True)
    args = parser.parse_args(argv)
    cfg = load_config(Path(args.config) if args.config else None)
    db = db_connect(args.db)
    state = _state()
    try:
        state.migrate(db)
        if args.cmd == "status":
            goals = state.list_autonomous_goals(db)
            ctl = state.get_control(db)
            print(json.dumps({
                "ok": True,
                "control": ctl,
                "config": {
                    "enabled": cfg.get("enabled"),
                    "limits": cfg.get("limits"),
                    "scheduler": cfg.get("scheduler"),
                },
                "autonomous_goals": goals,
            }, indent=2, default=str))
        elif args.cmd == "run-once":
            print(json.dumps(
                run_controller_once(db, config=cfg, goal_id=args.goal),
                indent=2, default=str,
            ))
        elif args.cmd == "pause":
            scope = "goal" if args.goal else "global"
            print(json.dumps(
                state.set_control(
                    db, "pause", scope=scope, goal_id=args.goal,
                    reason=args.reason, by_who="cli",
                ),
                indent=2, default=str,
            ))
        elif args.cmd == "resume":
            scope = "goal" if args.goal else "global"
            print(json.dumps(
                state.set_control(
                    db, "run", scope=scope, goal_id=args.goal,
                    reason="operator_resume", by_who="cli",
                ),
                indent=2, default=str,
            ))
        elif args.cmd == "stop":
            scope = "goal" if args.goal else "global"
            print(json.dumps(
                state.set_control(
                    db, "stop", scope=scope, goal_id=args.goal,
                    reason=args.reason, by_who="cli",
                ),
                indent=2, default=str,
            ))
        elif args.cmd == "decide":
            print(json.dumps(decide(db, args.goal, config=cfg), indent=2, default=str))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
