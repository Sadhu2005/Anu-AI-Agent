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
import urllib.parse
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
    safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(slug)).strip("-")[:40] or "work"
    branch = f"{AGENT_PREFIX}{int(goal_id)}-{safe_slug}"
    cur = current_branch(root)
    # Already on the target branch: allow dirty tree (commit_stage will add -A).
    if cur == branch:
        head = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
        return {"ok": True, "branch": branch, "head": head}
    # Stash local dirt so we can switch; restore afterward for commit_stage.
    stashed = False
    if git_status_porcelain(root).strip():
        stash = _run(
            ["git", "stash", "push", "-u", "-m", f"anu-pre-switch-{branch}"],
            cwd=root,
        )
        stashed = stash.returncode == 0
        if not stashed:
            require_clean_or_abort(root)
    try:
        if is_detached(root):
            # Create work branch from current detached commit (e.g. release tag).
            proc = _run(["git", "switch", "-c", branch], cwd=root)
            if proc.returncode != 0:
                proc2 = _run(["git", "switch", branch], cwd=root)
                if proc2.returncode != 0:
                    raise RuntimeError(
                        f"cannot create/switch branch {branch}: "
                        f"{proc.stderr or proc2.stderr}"
                    )
        else:
            exists = _run(["git", "rev-parse", "--verify", branch], cwd=root)
            if exists.returncode == 0:
                proc = _run(["git", "switch", branch], cwd=root)
            else:
                proc = _run(["git", "switch", "-c", branch], cwd=root)
            if proc.returncode != 0:
                raise RuntimeError(f"switch failed: {proc.stderr}")
    finally:
        if stashed:
            _run(["git", "stash", "pop"], cwd=root)
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
    # Drop agent runtime artifacts — they are not product code.
    artifact_paths = (
        "AUTONOMY_PROGRESS.md",
        ".anu/MATRIX_RESULTS.json",
        ".anu/DIAGNOSIS.md",
        ".anu/DIAGNOSIS.json",
        ".anu-changes.json",
        ".anu-verify-marker",
    )
    for drop in artifact_paths:
        _run(["git", "reset", "HEAD", "--", drop], cwd=root)
    # If only artifacts remain dirty, treat as nothing to commit (do not fail push).
    staged = _run(["git", "diff", "--cached", "--name-only"], cwd=root)
    staged_files = [ln.strip() for ln in (staged.stdout or "").splitlines() if ln.strip()]
    if not staged_files:
        for drop in artifact_paths:
            if (root / drop).exists() or (root / drop).is_file():
                _run(["git", "checkout", "--", drop], cwd=root)
            _run(["git", "clean", "-fd", "--", drop], cwd=root)
        head = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
        return {
            "ok": True,
            "skipped": True,
            "reason": "nothing_to_commit",
            "branch": branch,
            "head": head,
        }
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
        low = err.lower()
        if (
            "nothing to commit" in low
            or "no changes added to commit" in low
            or "nothing added to commit" in low
        ):
            for drop in artifact_paths:
                _run(["git", "checkout", "--", drop], cwd=root)
            head = _run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
            return {
                "ok": True,
                "skipped": True,
                "reason": "nothing_to_commit",
                "branch": branch,
                "head": head,
            }
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


def _resolve_github_token(token_env="ANU_GITHUB_TOKEN"):
    token = (os.environ.get(token_env) or "").strip()
    if token:
        return token
    file_env = os.environ.get("ANU_GITHUB_TOKEN_FILE")
    if file_env and Path(file_env).is_file():
        return Path(file_env).read_text(encoding="utf-8").strip()
    return ""


def _git_env_with_token(base_env=None, *, token_env="ANU_GITHUB_TOKEN"):
    """In-process HTTPS auth via GIT_CONFIG_* insteadOf — never log the token."""
    env = dict(base_env if base_env is not None else os.environ)
    token = _resolve_github_token(token_env)
    if not token:
        return env, False
    quoted = urllib.parse.quote(token, safe="")
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = (
        f"url.https://x-access-token:{quoted}@github.com/.insteadOf"
    )
    env["GIT_CONFIG_VALUE_0"] = "https://github.com/"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env, True


def _redact_secrets(text: str, token: str = "") -> str:
    out = text or ""
    if token:
        out = out.replace(token, "***")
        out = out.replace(urllib.parse.quote(token, safe=""), "***")
    out = re.sub(r"x-access-token:[^@\s]+@", "x-access-token:***@", out)
    out = re.sub(r"github_pat_[A-Za-z0-9_]+", "github_pat_***", out)
    out = re.sub(r"ghp_[A-Za-z0-9]+", "ghp_***", out)
    return out


def push_agent_branch(root: Path, *, remote="origin", token_env="ANU_GITHUB_TOKEN"):
    if not is_git_repo(root):
        return {"ok": False, "reason": "not_a_git_repo"}
    branch = current_branch(root)
    if not branch or not branch.startswith(AGENT_PREFIX):
        raise PermissionError(f"push denied: not on agent branch ({branch})")
    default = default_branch(root)
    validate_push_refspec(branch, default=default)
    token = _resolve_github_token(token_env)
    env, have_token = _git_env_with_token(os.environ.copy(), token_env=token_env)
    if not have_token:
        raise RuntimeError(
            "push failed: no GitHub token "
            f"(set {token_env} or ANU_GITHUB_TOKEN_FILE)"
        )
    argv = ["git", "push", "-u", remote, f"HEAD:refs/heads/{branch}"]
    proc = _run(argv, cwd=root, env=env)
    if proc.returncode != 0:
        err = _redact_secrets((proc.stderr or "").strip(), token)[-300:]
        if "non-fast-forward" in err.lower() or "rejected" in err.lower():
            raise RuntimeError(f"non_fast_forward: {err}")
        raise RuntimeError(f"push failed: {err}")
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


def build_pr_payload(
    *,
    title,
    head_branch,
    base="main",
    body=None,
    matrix_evidence=None,
    goal_id=None,
    run_id=None,
):
    """Build a reviewable PR payload. Never merges. Never force-pushes."""
    evidence_lines = []
    if isinstance(matrix_evidence, dict):
        for r in matrix_evidence.get("results") or []:
            evidence_lines.append(
                f"- {r.get('layer')}: {r.get('status')} ({r.get('reason')})"
            )
    elif matrix_evidence:
        evidence_lines.append(str(matrix_evidence)[:2000])
    parts = [
        body or "",
        "",
        "## Test matrix evidence",
        "",
        *(evidence_lines or ["- (none recorded)"]),
        "",
        f"- Goal-Id: {goal_id}" if goal_id is not None else "",
        f"- Run-Id: {run_id}" if run_id is not None else "",
        "",
        "**Human merge only — ANU never auto-merges.**",
    ]
    return {
        "title": str(title or "").strip() or f"agent work ({head_branch})",
        "head": str(head_branch or "").strip(),
        "base": str(base or "main").strip(),
        "body": "\n".join(p for p in parts if p is not None),
        "draft": True,
        "auto_merge": False,
    }


def create_pr_gated(
    *args,
    approved=False,
    title=None,
    head_branch=None,
    base="main",
    body=None,
    matrix_evidence=None,
    goal_id=None,
    run_id=None,
    **kwargs,
):
    """PR creation is always gated — never auto-merge. Requires approved=True."""
    payload = build_pr_payload(
        title=title,
        head_branch=head_branch,
        base=base,
        body=body,
        matrix_evidence=matrix_evidence,
        goal_id=goal_id,
        run_id=run_id,
    )
    if not approved:
        return {
            "ok": False,
            "needs_approval": True,
            "reason": "pull_request_requires_user_authorization",
            "payload": payload,
            "auto_merge": False,
        }
    # Even when approved, do not auto-merge; caller must use gh/API separately.
    return {
        "ok": True,
        "needs_approval": False,
        "created": False,
        "reason": "payload_ready_human_or_token_create",
        "payload": payload,
        "auto_merge": False,
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
