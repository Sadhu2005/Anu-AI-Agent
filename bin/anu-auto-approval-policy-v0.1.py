#!/usr/bin/env python3
"""ANU Auto-Approval Policy v0.1 (V0.9.1).

Policy-based auto-approve for autonomous goals.
Always deny deploy / secrets / force-push / default-branch push / delete / new_repo.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"

CAPABILITY_TO_CLASS = {
    "research": "research",
    "github": "github_read",
    "github_read": "github_read",
    "python_test": "test",
    "python_compile": "test",
    "mutation": "mutation",
    "safe_mutation": "mutation",
    "filesystem.write": "mutation",
    "major_mutation": "mutation",
    "git_commit": "commit",
    "commit": "commit",
    "git_push": "push_agent_branch",  # refined by refspec check
    "push_agent_branch": "push_agent_branch",
    "deploy": "deploy",
    "production_deploy": "deploy",
    "production_deployment": "deploy",
    "package": "prod_infra",
    "docker": "prod_infra",
    "sudo": "prod_infra",
    "shell": "prod_infra",
    "secrets": "secrets",
    "force_push": "force_push",
    "push_default_branch": "push_default_branch",
    "delete": "delete",
    "new_repo": "new_repo",
    "browser": "research",
    "download": "research",
    "model": "research",
}


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


def load_autonomy_config(path=None):
    ctrl = _load("anu-autonomy-controller-v0.1.py", "anu_ctrl_policy")
    return ctrl.load_config(Path(path) if path else None)


def map_capability_to_class(capability, *, refspec=None, force=False):
    name = str(capability or "").strip()
    if force or (refspec and ("--force" in str(refspec) or "-f" in str(refspec).split())):
        return "force_push"
    if name in ("git_push", "push_agent_branch"):
        ref = str(refspec or "")
        if ref and not re.search(r"(^|[\s:])agent/", ref):
            return "push_default_branch"
        return "push_agent_branch"
    return CAPABILITY_TO_CLASS.get(name, "mutation" if name else "deploy")


def _goal_is_autonomous(db, goal_id):
    if goal_id is None:
        return False
    try:
        state = _load("anu-autonomy-state-v0.1.py", "anu_state_policy")
        state.migrate(db)
        obj = state.get_objective(db, goal_id)
        return bool(obj and obj.get("autonomous"))
    except Exception:
        row = db.execute(
            "SELECT autonomous FROM goals WHERE id = ?", (int(goal_id),)
        ).fetchone()
        if row is None:
            return False
        return bool(row[0] if not isinstance(row, sqlite3.Row) else row["autonomous"])


def _project_allowlisted(db, project_key=None, goal_id=None):
    """GitHub owner allow-list check when repository is github."""
    try:
        if project_key is None and goal_id is not None:
            row = db.execute(
                """SELECT p.project_key, p.repository FROM goals g
                   JOIN projects p ON p.id = g.project_id WHERE g.id = ?""",
                (int(goal_id),),
            ).fetchone()
            if row is None:
                return True
            project_key = row[0] if not isinstance(row, sqlite3.Row) else row["project_key"]
            repository = row[1] if not isinstance(row, sqlite3.Row) else row["repository"]
        else:
            row = db.execute(
                "SELECT repository FROM projects WHERE project_key = ?",
                (project_key,),
            ).fetchone()
            repository = (
                None if row is None
                else (row[0] if not isinstance(row, sqlite3.Row) else row["repository"])
            )
        if not repository or repository == "local":
            return True
        if "github.com" not in str(repository):
            return False
        m = re.search(r"github\.com[:/]([^/]+)/", str(repository))
        if not m:
            return False
        owner = m.group(1)
        gh = _load("anu-github-v0.1.py", "anu_gh_policy")
        cfg, _ = gh.load_config()
        gh._require_owner(owner, cfg)
        return True
    except Exception:
        return False


def scan_secrets(text, patterns=None):
    cfg = load_autonomy_config()
    pats = patterns or (cfg.get("policy") or {}).get("secret_patterns") or []
    hits = []
    for pat in pats:
        try:
            if re.search(pat, text or ""):
                hits.append(pat)
        except re.error:
            continue
    return hits


def path_escape(paths, project_root=None):
    bad = []
    for p in paths or []:
        s = str(p).replace("\\", "/")
        if s.startswith("/") or s.startswith("..") or "/../" in f"/{s}/":
            bad.append(s)
    return bad


def protected_path_hit(paths, protected=None):
    cfg = load_autonomy_config()
    prefixes = protected or (cfg.get("policy") or {}).get("protected_paths") or []
    hits = []
    for p in paths or []:
        s = str(p).replace("\\", "/")
        for pref in prefixes:
            if s.startswith(pref) or f"/{pref}" in f"/{s}":
                hits.append(s)
                break
    return hits


def _approvals_last_hour(db):
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    try:
        row = db.execute(
            """SELECT COUNT(*) FROM approvals
               WHERE status = 'approved' AND decided_by LIKE 'policy:%'
                 AND COALESCE(decided_at, created_at) >= ?""",
            (since,),
        ).fetchone()
        return int(row[0])
    except sqlite3.Error:
        return 0


def evaluate(
    db,
    *,
    capability,
    goal_id=None,
    project_key=None,
    refspec=None,
    force=False,
    changed_paths=None,
    diff_text=None,
    config=None,
):
    """Return {decision: approve|deny|defer, class, rule, reason}."""
    cfg = config or load_autonomy_config()
    policy = cfg.get("policy") or {}
    auto_classes = set(policy.get("auto_approve_classes") or [])
    deny_classes = set(policy.get("always_deny_classes") or [])

    if not cfg.get("auto_approve", True):
        return {
            "decision": "defer",
            "class": None,
            "rule": "kill_switch",
            "reason": "autonomy.auto_approve is false",
        }

    pclass = map_capability_to_class(capability, refspec=refspec, force=force)
    if pclass in deny_classes:
        return {
            "decision": "deny",
            "class": pclass,
            "rule": "always_deny",
            "reason": f"class {pclass} is always denied",
        }

    if goal_id is not None and not _goal_is_autonomous(db, goal_id):
        return {
            "decision": "defer",
            "class": pclass,
            "rule": "not_autonomous",
            "reason": "goal is not marked autonomous",
        }

    if not _project_allowlisted(db, project_key=project_key, goal_id=goal_id):
        return {
            "decision": "deny",
            "class": pclass,
            "rule": "owner_not_allowlisted",
            "reason": "GitHub owner not in allow_owners",
        }

    escapes = path_escape(changed_paths)
    if escapes:
        return {
            "decision": "deny",
            "class": "mutation",
            "rule": "path_escape",
            "reason": f"paths outside workspace: {escapes}",
        }

    protected = protected_path_hit(changed_paths)
    if protected and pclass in ("mutation", "commit"):
        return {
            "decision": "deny",
            "class": pclass,
            "rule": "protected_path",
            "reason": f"protected paths: {protected}",
        }

    secrets = scan_secrets(diff_text or "")
    if secrets:
        return {
            "decision": "deny",
            "class": "secrets",
            "rule": "secret_scan",
            "reason": "secret-like patterns in diff",
        }

    limit = int(policy.get("rate_limit_approvals_per_hour") or 60)
    if _approvals_last_hour(db) >= limit:
        return {
            "decision": "defer",
            "class": pclass,
            "rule": "rate_limit",
            "reason": f"auto-approvals hit {limit}/hour",
        }

    if pclass in auto_classes:
        return {
            "decision": "approve",
            "class": pclass,
            "rule": f"auto:{pclass}",
            "reason": f"policy auto-approves {pclass}",
        }

    return {
        "decision": "defer",
        "class": pclass,
        "rule": "no_rule",
        "reason": f"class {pclass} not in auto_approve_classes",
    }


def auto_decide_approval(db, approval_id, *, config=None):
    """Hook: given a pending approval row, approve/deny/leave pending."""
    approval = _load("anu-approval-service-v0.1.py", "anu_approval_policy")
    row = None
    if hasattr(approval, "get_approval"):
        row = approval.get_approval(db, approval_id)
    if row is None:
        raw = db.execute(
            "SELECT * FROM approvals WHERE id = ?", (int(approval_id),)
        ).fetchone()
        if raw is None:
            return {"ok": False, "error": "not_found"}
        row = dict(raw) if isinstance(raw, sqlite3.Row) else {"id": raw[0]}

    status = str(row.get("status") or "").lower()
    if status != "pending":
        return {"ok": True, "skipped": True, "reason": "not_pending", "status": status}

    capability = row.get("capability") or row.get("action") or row.get("operation")
    goal_id = row.get("goal_id")
    task_id = row.get("task_id")
    if goal_id is None and task_id is not None:
        t = db.execute("SELECT goal_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if t is not None:
            goal_id = t[0] if not isinstance(t, sqlite3.Row) else t["goal_id"]

    result = evaluate(
        db,
        capability=capability,
        goal_id=goal_id,
        config=config,
    )
    if result["decision"] == "approve":
        by = f"policy:{result['rule']}"
        if hasattr(approval, "approve"):
            approval.approve(db, approval_id, decided_by=by)
        else:
            db.execute(
                """UPDATE approvals SET status='approved', decided_by=?, decided_at=?
                   WHERE id=?""",
                (by, now_iso(), int(approval_id)),
            )
            db.commit()
        try:
            state = _load("anu-autonomy-state-v0.1.py", "anu_state_policy2")
            state.emit_autonomy_event(
                db, goal_id, "AUTONOMY_CONTINUE",
                f"auto-approved approval {approval_id}",
                {"approval_id": approval_id, **result},
            )
        except Exception:
            pass
        return {"ok": True, "decision": "approve", "by": by, "result": result}

    if result["decision"] == "deny":
        by = f"policy:{result['rule']}"
        if hasattr(approval, "reject"):
            approval.reject(
                db, approval_id, decided_by=by, note=result.get("reason")
            )
        else:
            db.execute(
                """UPDATE approvals SET status='rejected', decided_by=?, decided_at=?
                   WHERE id=?""",
                (by, now_iso(), int(approval_id)),
            )
            db.commit()
        try:
            state = _load("anu-autonomy-state-v0.1.py", "anu_state_policy3")
            state.set_control(
                db, "pause", scope="goal", goal_id=goal_id,
                reason=result.get("reason"), by_who=by,
            )
            state.emit_autonomy_event(
                db, goal_id, "AUTONOMY_PAUSE",
                f"policy denied approval {approval_id}",
                {"approval_id": approval_id, **result},
            )
        except Exception:
            pass
        return {"ok": True, "decision": "deny", "by": by, "result": result}

    return {"ok": True, "decision": "defer", "result": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Auto-Approval Policy v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--capability", required=True)
    ev.add_argument("--goal-id", type=int, default=None)
    ev.add_argument("--refspec", default=None)
    ad = sub.add_parser("auto-decide")
    ad.add_argument("--approval-id", type=int, required=True)
    args = parser.parse_args(argv)
    state = _load("anu-autonomy-state-v0.1.py", "anu_state_policy_cli")
    db = state.db_connect(args.db)
    try:
        if args.cmd == "evaluate":
            print(json.dumps(
                evaluate(db, capability=args.capability, goal_id=args.goal_id, refspec=args.refspec),
                indent=2,
            ))
        else:
            print(json.dumps(auto_decide_approval(db, args.approval_id), indent=2, default=str))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
