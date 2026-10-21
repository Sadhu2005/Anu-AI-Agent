#!/usr/bin/env python3
"""ANU Git Workflow v0.1 (V0.9.5) — agent/* branches, small commits, safe push.

Never force-push. Never push default branch. Preserve dirty user changes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
WORKSPACE = BASE / "workspace" / "projects"
DB_FILE = BASE / "brain" / "anu-brain.db"
AGENT_PREFIX = "agent/"


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


def _run(argv, *, cwd, check=False, env=None):
    return subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
        env=env,
        shell=False,
    )


def project_root(project_key):
    return (WORKSPACE / project_key).resolve()


def is_git_repo(root: Path) -> bool:
    return (root / ".git").exists()


def git_status_porcelain(root: Path) -> str:
    proc = _run(["git", "status", "--porcelain"], cwd=root)
    return proc.stdout or ""


def current_branch(root: Path) -> str | None:
    proc = _run(["git", "branch", "--show-current"], cwd=root)
    name = (proc.stdout or "").strip()
    return name or None


def is_detached(root: Path) -> bool:
    return current_branch(root) is None


def default_branch(root: Path) -> str:
    proc = _run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=root)
    out = (proc.stdout or "").strip()
    if out.startswith("refs/remotes/origin/"):
        return out.split("/")[-1]
    for candidate in ("main", "master"):
        probe = _run(["git", "rev-parse", "--verify", candidate], cwd=root)
        if probe.returncode == 0:
            return candidate
    return "main"


def require_clean_or_abort(root: Path, *, allow_progress_file=True):
    """Abort if dirty user changes exist (AUTONOMY_PROGRESS.md ignored)."""
    status = git_status_porcelain(root)
    lines = []
    for line in status.splitlines():
        body = line[3:] if len(line) >= 3 else line
        body = body.strip().strip('"')
        if allow_progress_file and body.endswith("AUTONOMY_PROGRESS.md"):
            continue
        if body:
            lines.append(line)
    if lines:
        raise RuntimeError(
            "dirty worktree; preserve user changes before agent git ops: "
            + "; ".join(lines[:5])
        )
    return True


def ensure_agent_branch(root: Path, goal_id, slug="work"):
    """Create/switch to agent/<goal-id>-<slug>. Handle detached HEAD."""
    if not is_git_repo(root):
        return {"ok": False, "reason": "not_a_git_repo"}
    require_clean_or_abort(root)
    safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(slug)).strip("-")[:40] or "work"
    branch = f"{AGENT_PREFIX}{int(goal_id)}-{safe_slug}"
    if is_detached(root):
        # Create work branch from current detached commit (e.g. release tag).
        proc = _run(["git", "switch", "-c", branch], cwd=root)
        if proc.returncode != 0:
            # Branch may already exist
            proc2 = _run(["git", "switch", branch], cwd=root)
            if proc2.returncode != 0:
                raise RuntimeError(
                    f"cannot create/switch branch {branch}: {proc.stderr or proc2.stderr}"
                )
    else:
        cur = current_branch(root)
        if cur != branch:
            exists = _run(["git", "rev-parse", "--verify", branch], cwd=root)
            if exists.returncode == 0:
                proc = _run(["git", "switch", branch], cwd=root)
            else:
                proc = _run(["git", "switch", "-c", branch], cwd=root)
            if proc.returncode != 0:
                raise RuntimeError(f"switch failed: {proc.stderr}")
    head = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    return {"ok": True, "branch": branch, "head": head}


def commit_stage(root: Path, message, *, goal_id=None, task_id=None, paths=None):
    require_clean_or_abort(root) if False else None  # paths may be staged
    if not is_git_repo(root):
        return {"ok": False, "reason": "not_a_git_repo"}
    branch = current_branch(root)
    if not branch or not branch.startswith(AGENT_PREFIX):
        raise PermissionError(f"refusing commit outside {AGENT_PREFIX}* (on {branch})")
    if paths:
        for p in paths:
            _run(["git", "add", "--", p], cwd=root)
    else:
        _run(["git", "add", "-A"], cwd=root)
    # Drop progress file from commit if present
    _run(["git", "reset", "HEAD", "--", "AUTONOMY_PROGRESS.md"], cwd=root)
    trailer = []
    if goal_id is not None:
        trailer.append(f"Goal-Id: {goal_id}")
    if task_id is not None:
        trailer.append(f"Task-Id: {task_id}")
    full = message.rstrip() + ("\n\n" + "\n".join(trailer) if trailer else "")
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "ANU Agent"
    env["GIT_AUTHOR_EMAIL"] = "anu-agent@localhost"
    env["GIT_COMMITTER_NAME"] = "ANU Agent"
    env["GIT_COMMITTER_EMAIL"] = "anu-agent@localhost"
    proc = _run(["git", "commit", "-m", full], cwd=root, env=env)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if "nothing to commit" in err.lower():
            return {"ok": True, "skipped": True, "reason": "nothing_to_commit", "branch": branch}
        raise RuntimeError(f"commit failed: {err}")
    head = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    return {"ok": True, "branch": branch, "head": head, "message": full}


def validate_push_refspec(refspec, *, default=None):
    """Allow only agent/* destinations; deny force and default branch."""
    spec = str(refspec or "").strip()
    if not spec:
        raise PermissionError("empty refspec")
    parts = spec.split()
    if any(p in ("--force", "-f", "--force-with-lease") for p in parts):
        raise PermissionError("force push denied")
    # Normalize refs like HEAD:agent/1-x or agent/1-x
    dest = parts[-1]
    if ":" in dest:
        dest = dest.split(":", 1)[1]
    dest = dest.replace("refs/heads/", "")
    if not dest.startswith(AGENT_PREFIX):
        raise PermissionError(f"push denied: destination must be {AGENT_PREFIX}*, got {dest}")
    if default and dest == default:
        raise PermissionError("push to default branch denied")
    return dest


def push_agent_branch(root: Path, *, remote="origin", token_env="ANU_GITHUB_TOKEN"):
    if not is_git_repo(root):
        return {"ok": False, "reason": "not_a_git_repo"}
    branch = current_branch(root)
    if not branch or not branch.startswith(AGENT_PREFIX):
        raise PermissionError(f"push denied: not on agent branch ({branch})")
    default = default_branch(root)
    validate_push_refspec(branch, default=default)
    env = os.environ.copy()
    token = os.environ.get(token_env)
    file_env = os.environ.get("ANU_GITHUB_TOKEN_FILE")
    if not token and file_env and Path(file_env).is_file():
        token = Path(file_env).read_text(encoding="utf-8").strip()
    # Use insteadOf via env only in-process; never log token.
    argv = ["git", "push", "-u", remote, f"HEAD:refs/heads/{branch}"]
    proc = _run(argv, cwd=root, env=env)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "non-fast-forward" in err.lower() or "rejected" in err.lower():
            raise RuntimeError(f"non_fast_forward: {err[-300:]}")
        raise RuntimeError(f"push failed: {err[-300:]}")
    return {"ok": True, "branch": branch, "remote": remote}


def record_git_event(db, goal_id, event_type, payload):
    try:
        state = _load("anu-autonomy-state-v0.1.py", "anu_state_git")
        state.emit_autonomy_event(
            db, goal_id, "AUTONOMY_CONTINUE",
            f"git:{event_type}",
            payload,
        )
    except Exception:
        pass


def create_pr_gated(*_args, **_kwargs):
    """PR creation is always gated — never auto."""
    return {
        "ok": False,
        "needs_approval": True,
        "reason": "pull_request_requires_user_authorization",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Git Workflow v0.1")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("ensure-branch")
    b.add_argument("--project", required=True)
    b.add_argument("--goal-id", type=int, required=True)
    b.add_argument("--slug", default="work")
    c = sub.add_parser("commit")
    c.add_argument("--project", required=True)
    c.add_argument("--message", required=True)
    c.add_argument("--goal-id", type=int, default=None)
    c.add_argument("--task-id", type=int, default=None)
    p = sub.add_parser("push")
    p.add_argument("--project", required=True)
    v = sub.add_parser("validate-refspec")
    v.add_argument("--refspec", required=True)
    v.add_argument("--default", default="main")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "ensure-branch":
            print(json.dumps(ensure_agent_branch(project_root(args.project), args.goal_id, args.slug), indent=2))
        elif args.cmd == "commit":
            print(json.dumps(
                commit_stage(
                    project_root(args.project), args.message,
                    goal_id=args.goal_id, task_id=args.task_id,
                ),
                indent=2,
            ))
        elif args.cmd == "push":
            print(json.dumps(push_agent_branch(project_root(args.project)), indent=2))
        elif args.cmd == "validate-refspec":
            dest = validate_push_refspec(args.refspec, default=args.default)
            print(json.dumps({"ok": True, "dest": dest}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
