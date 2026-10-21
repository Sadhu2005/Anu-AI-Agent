#!/usr/bin/env python3
"""ANU Post-ACCEPT git hook (V0.9.12) — matrix gate → commit → push agent/*.

Cadence: verification ACCEPT → run test matrix → ensure agent branch →
commit → push. Push failure returns waiting/PAUSE signal (not COMPLETE).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _load(name, mod_name):
    for candidate in (BASE / "bin" / name, _HERE / name, _REPO / "bin" / name):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(mod_name, candidate)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError(name)


def _audit(db, event_type, message, metadata=None, goal_id=None, task_id=None):
    try:
        db.execute(
            """INSERT INTO agent_events (task_id, goal_id, event_type, message, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (
                task_id,
                goal_id,
                event_type,
                message,
                json.dumps(metadata or {}, default=str),
            ),
        )
        db.commit()
    except sqlite3.Error:
        pass


def _workspace_for_goal(db, goal_id):
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


def _load_autonomy_config():
    try:
        ctrl = _load("anu-autonomy-controller-v0.1.py", "anu_ctrl_post")
        return ctrl.load_config()
    except Exception:
        return {}


def pushes_last_hour(db, goal_id) -> int:
    since = datetime.now(timezone.utc).isoformat()
    # naive: count GIT_PUSH_OK in last hour via string compare on ISO stamps
    try:
        row = db.execute(
            """SELECT COUNT(*) FROM agent_events
               WHERE goal_id = ? AND event_type = 'GIT_PUSH_OK'
                 AND created_at >= datetime('now', '-1 hour')""",
            (int(goal_id),),
        ).fetchone()
        return int(row[0])
    except sqlite3.Error:
        return 0


def after_accept(
    db,
    *,
    goal_id,
    task_id=None,
    message=None,
    slice_slug="work",
    skip_matrix=False,
    config=None,
) -> dict:
    """Run matrix (unless skipped), commit, push. Returns status dict."""
    cfg = config or _load_autonomy_config()
    git_cfg = cfg.get("git") or {}
    if not cfg.get("push_after_accept", git_cfg.get("push_after_accept", True)):
        return {"ok": True, "skipped": True, "reason": "push_after_accept_disabled"}

    proj = _workspace_for_goal(db, goal_id)
    if not proj:
        return {"ok": False, "error": "no_project", "pause": True}
    root = Path(proj.get("workspace") or "")
    if not root.is_dir():
        # Fall back to ANU workspace layout
        key = proj.get("project_key")
        alt = BASE / "workspace" / "projects" / str(key)
        root = alt if alt.is_dir() else root
    if not root.is_dir():
        return {"ok": False, "error": f"workspace_missing:{root}", "pause": True}

    # Rate limit pushes
    limit = int((cfg.get("policy") or {}).get("rate_limit_pushes_per_hour") or 30)
    if pushes_last_hour(db, goal_id) >= limit:
        _audit(db, "GIT_PUSH_FAIL", "rate_limit", {"limit": limit}, goal_id, task_id)
        return {"ok": False, "error": "push_rate_limit", "pause": True, "waiting": True}

    matrix_mod = _load("anu-test-matrix-v0.1.py", "anu_matrix_post")
    diagnose_mod = _load("anu-failure-diagnose-v0.1.py", "anu_diag_post")
    git_mod = _load("anu-git-workflow-v0.1.py", "anu_git_post")

    required = list((cfg.get("test_matrix") or {}).get("required_layers") or [])
    project_overrides = (cfg.get("test_matrix") or {}).get("by_project_key") or {}
    key = proj.get("project_key")
    if key and key in project_overrides:
        required = list(project_overrides[key].get("required_layers") or required)

    matrix_report = None
    if not skip_matrix:
        matrix_report = matrix_mod.run_matrix(root, required_layers=required or None)
        matrix_mod.write_matrix_artifact(root, matrix_report)
        if not matrix_report.get("ok"):
            diagnosis = diagnose_mod.diagnose_matrix_report(matrix_report)
            path = diagnose_mod.write_diagnosis(root, diagnosis)
            _audit(
                db,
                "MATRIX_FAIL",
                diagnosis.get("summary") or "matrix failed",
                {"diagnosis": diagnosis, "path": path, "failed": matrix_report.get("failed")},
                goal_id,
                task_id,
            )
            return {
                "ok": False,
                "error": "matrix_failed",
                "matrix": matrix_report,
                "diagnosis": diagnosis,
                "replan": True,
                "failure_class": diagnosis.get("primary_class"),
            }

    # Ensure agent branch
    try:
        branch_info = git_mod.ensure_agent_branch(root, goal_id, slug=slice_slug or "work")
    except Exception as exc:
        _audit(db, "GIT_PUSH_FAIL", str(exc), {"phase": "ensure_branch"}, goal_id, task_id)
        return {"ok": False, "error": str(exc), "pause": True}

    commit_msg = message or f"goal-{goal_id}: verified slice"
    if matrix_report:
        parts = [
            f"{r['layer']}={r['status'].lower()}"
            for r in matrix_report.get("results") or []
        ]
        commit_msg = f"{commit_msg} [{', '.join(parts)}]"

    try:
        commit_res = git_mod.commit_stage(
            root, commit_msg, goal_id=goal_id, task_id=task_id
        )
    except Exception as exc:
        _audit(db, "GIT_PUSH_FAIL", str(exc), {"phase": "commit"}, goal_id, task_id)
        return {"ok": False, "error": str(exc), "pause": True}

    if commit_res.get("skipped"):
        # No product files changed (matrix/.anu artifacts are excluded from commits).
        _audit(
            db,
            "GIT_PUSH_SKIP",
            "nothing_to_commit (no code changes; .anu artifacts not committed)",
            {"commit": commit_res, "matrix": (matrix_report or {}).get("summary")},
            goal_id,
            task_id,
        )
        return {
            "ok": True,
            "skipped": True,
            "reason": "nothing_to_commit",
            "branch": branch_info.get("branch"),
            "commit": commit_res,
            "matrix": (matrix_report or {}).get("summary"),
        }

    try:
        push_res = git_mod.push_agent_branch(root)
    except Exception as exc:
        _audit(
            db,
            "GIT_PUSH_FAIL",
            str(exc),
            {"phase": "push", "branch": branch_info.get("branch")},
            goal_id,
            task_id,
        )
        return {
            "ok": False,
            "error": str(exc),
            "pause": True,
            "waiting": True,
            "committed": commit_res,
        }

    head = push_res.get("head") or commit_res.get("head") or branch_info.get("head")
    _audit(
        db,
        "GIT_PUSH_OK",
        f"pushed {branch_info.get('branch')} @{head}",
        {
            "branch": branch_info.get("branch"),
            "head": head,
            "matrix": (matrix_report or {}).get("summary"),
            "commit": commit_res,
            "push": push_res,
        },
        goal_id,
        task_id,
    )
    return {
        "ok": True,
        "branch": branch_info.get("branch"),
        "head": head,
        "commit": commit_res,
        "push": push_res,
        "matrix": matrix_report,
    }
