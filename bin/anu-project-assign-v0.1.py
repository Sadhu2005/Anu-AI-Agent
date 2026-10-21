#!/usr/bin/env python3
"""ANU Project Assign v0.1 (V0.8.13) — register a repo, then assign a goal.

Operator CLI only. Does not invent files or mutate code.

Sources:
- existing workspace directory (``--project-key``)
- allow-listed GitHub ``owner/repo`` (HTTPS clone into workspace)

Arbitrary remotes, SSH, and non-allow-listed owners are rejected.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"
WORKSPACE = BASE / "workspace" / "projects"

GITHUB_SPEC_RE = re.compile(
    r"^(?:https://github\.com/)?([A-Za-z0-9][A-Za-z0-9._-]*)/"
    r"([A-Za-z0-9][A-Za-z0-9._-]*?)(?:\.git)?/?$"
)


def _load(name, module_name):
    for candidate in (BASE / "bin" / name, _HERE / name, _REPO / "bin" / name):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(module_name, candidate)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError(name)


def _pi():
    return _load("anu-project-intelligence-v0.1.py", "anu_pi_v0813_assign")


def _goals():
    return _load("anu-goal-service-v0.1.py", "anu_goals_v0813_assign")


def _github():
    return _load("anu-github-v0.1.py", "anu_github_v0813_assign")


def _cycle():
    return _load("anu-goal-cycle-v0.1.py", "anu_cycle_v0813_assign")


def parse_github_spec(spec):
    """Return (owner, repo) from owner/repo or https://github.com/owner/repo."""
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("github spec must be a non-empty string")
    raw = spec.strip()
    if raw.startswith("git@") or raw.startswith("ssh://"):
        raise PermissionError("ssh remotes are not allowed")
    if raw.startswith("http://"):
        raise PermissionError("only https github.com remotes are allowed")
    if raw.startswith("https://"):
        parsed = urlparse(raw)
        if parsed.hostname != "github.com":
            raise PermissionError(f"host not allowed: {parsed.hostname}")
        raw = parsed.path.lstrip("/")
    match = GITHUB_SPEC_RE.fullmatch(raw)
    if not match:
        raise ValueError(f"invalid github spec: {spec}")
    return match.group(1), match.group(2)


def github_https_url(owner, repo):
    return f"https://github.com/{owner}/{repo}.git"


def require_allowlisted_owner(owner, *, github_cfg=None):
    gh = _github()
    cfg = github_cfg
    if cfg is None:
        cfg, _path = gh.load_config()
    return gh._require_owner(owner, cfg)


def clone_github_repo(dest, url, *, runner=None):
    """Shallow clone an allow-listed HTTPS GitHub URL into dest."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"workspace already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "safe.directory=*",
        "clone",
        "--depth",
        "1",
        "--",
        url,
        str(dest),
    ]
    run = runner or subprocess.run
    process = run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        shell=False,
    )
    if getattr(process, "returncode", 1) != 0:
        err = (getattr(process, "stderr", "") or "")[-400:]
        raise RuntimeError(f"git clone failed: {err or 'unknown error'}")
    return {"ok": True, "path": str(dest), "url": url}


def register_project(
    db,
    *,
    project_key=None,
    github=None,
    clone=True,
    workspace_root=None,
    config_path=None,
    runner=None,
    github_cfg=None,
):
    """Register YAML + brain row; optionally clone an allow-listed GitHub repo."""
    pi = _pi()
    root = Path(workspace_root) if workspace_root else WORKSPACE
    owner = repo = None
    repository = "local"
    if github:
        owner, repo = parse_github_spec(github)
        require_allowlisted_owner(owner, github_cfg=github_cfg)
        project_key = project_key or repo
        repository = github_https_url(owner, repo)
    if not project_key:
        raise ValueError("project_key or github spec is required")
    paths = _load("anu-project-paths-v0.1.py", "anu_paths_v0813_assign")
    key = paths.validate_project_name(project_key)
    dest = (root / key).resolve()
    try:
        dest.relative_to(Path(root).resolve())
    except ValueError as exc:
        raise PermissionError("project path is outside workspace") from exc

    yaml_result = pi.add_yaml_project(
        key,
        name=key,
        repository=repository,
        config_path=config_path,
    )
    cloned = False
    if github and clone:
        if not dest.exists() or not any(dest.iterdir()):
            clone_github_repo(dest, repository, runner=runner)
            cloned = True
        else:
            cloned = False
    dest.mkdir(parents=True, exist_ok=True)

    config = pi.load_projects_config(config_path or yaml_result["path"])
    row = pi.ensure_project_row(
        db,
        key,
        name=key,
        repository=repository,
        workspace=str(dest),
        config=config,
    )
    return {
        "ok": True,
        "project_key": key,
        "project_id": row.get("id") if isinstance(row, dict) else None,
        "workspace": str(dest),
        "repository": repository,
        "yaml": yaml_result,
        "cloned": cloned,
        "owner": owner,
        "repo": repo,
    }


def assign_goal(
    db,
    project_key,
    description,
    *,
    expand=False,
    config_path=None,
    objective=None,
    criteria=None,
    autonomous=False,
):
    """Create a pending goal on a registered project; optionally expand it.

    V0.9: ``objective`` / ``criteria`` / ``autonomous`` attach autonomy state.
    """
    pi = _pi()
    goals = _goals()
    config = pi.load_projects_config(config_path) if config_path else None
    key = pi.require_registered(project_key, config)
    row = pi.ensure_project_row(db, key, config=config)
    project_id = row["id"] if isinstance(row, dict) else row[0]
    # Reject placeholder goals (e.g. literal "…" copied from docs).
    try:
        gen = _load("anu-change-generator-v0.1.py", "anu_gen_assign")
        if gen.is_placeholder_goal(description):
            raise ValueError(
                "goal description is empty or a placeholder; "
                "pass a real objective (e.g. 'Build a todo list with tests')"
            )
    except FileNotFoundError:
        if not (description or "").strip():
            raise ValueError("goal description must be non-empty")
    goal = goals.create_goal(db, description, project_id=project_id)
    autonomy_meta = None
    if autonomous or objective or criteria is not None:
        state = _load("anu-autonomy-state-v0.1.py", "anu_state_assign")
        state.migrate(db)
        obj_text = (objective or description or "").strip()
        crit = criteria
        if crit is None:
            crit = state.derive_default_criteria(obj_text)
        autonomy_meta = state.create_objective(
            db,
            goal["id"],
            obj_text,
            crit,
            autonomous=bool(autonomous),
        )
        goal = goals.get_goal(db, goal["id"]) or goal
    expand_result = None
    if expand:
        cycle = _cycle()
        # V0.9: autonomous goals always use the software_goal template planner.
        # The LLM planner often emits research-only graphs that never implement.
        if autonomous:
            planner = _load(
                "anu-template-planner-v0.1.py", "anu_template_planner_assign"
            )
            expand_result = planner.expand_goal(
                db,
                goal["id"],
                project=key,
                template_name="software_goal",
            )
        else:
            planner = cycle._load_planner()
            expand_result = planner.expand_goal(
                db,
                goal["id"],
                project=key,
            )
        try:
            first = cycle.start_first_ready_for_goal(db, goal["id"])
        except Exception as exc:
            first = {
                "started": False,
                "goal_id": goal["id"],
                "reason": f"{type(exc).__name__}: {exc}",
            }
        expand_result = {
            "template": expand_result.get("template"),
            "task_count": len(expand_result.get("tasks") or []),
            "first_ready": first,
        }
    return {
        "ok": True,
        "project_key": key,
        "project_id": project_id,
        "goal": goal,
        "autonomy": autonomy_meta,
        "expand": expand_result,
    }


def connect_project(
    db,
    *,
    description,
    project_key=None,
    github=None,
    clone=True,
    expand=False,
    workspace_root=None,
    config_path=None,
    runner=None,
    github_cfg=None,
    objective=None,
    criteria=None,
    autonomous=False,
):
    registered = register_project(
        db,
        project_key=project_key,
        github=github,
        clone=clone,
        workspace_root=workspace_root,
        config_path=config_path,
        runner=runner,
        github_cfg=github_cfg,
    )
    assigned = assign_goal(
        db,
        registered["project_key"],
        description,
        expand=expand,
        config_path=config_path,
        objective=objective,
        criteria=criteria,
        autonomous=autonomous,
    )
    return {
        "ok": True,
        "register": registered,
        "assign": assigned,
    }


def db_connect(path=None):
    pi = _pi()
    return pi.db_connect(path if path is not None else DB_FILE)


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Project Assign v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    parser.add_argument("--config", default=None, help="anu-projects.yaml path")
    parser.add_argument(
        "--workspace",
        default=None,
        help="workspace/projects root (default ANU workspace)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    reg = sub.add_parser("register")
    reg.add_argument("--project-key")
    reg.add_argument("--github", help="owner/repo or https://github.com/owner/repo")
    reg.add_argument("--no-clone", action="store_true")

    asg = sub.add_parser("assign")
    asg.add_argument("--project-key", required=True)
    asg.add_argument("--goal", required=True)
    asg.add_argument("--expand", action="store_true")
    asg.add_argument("--objective", default=None)
    asg.add_argument(
        "--criteria",
        default=None,
        help="semicolon-separated acceptance criteria",
    )
    asg.add_argument(
        "--autonomous",
        action="store_true",
        help="mark goal autonomous (V0.9 junior-dev mode)",
    )

    con = sub.add_parser("connect")
    con.add_argument("--project-key")
    con.add_argument("--github")
    con.add_argument("--goal", required=True)
    con.add_argument("--no-clone", action="store_true")
    con.add_argument("--expand", action="store_true")
    con.add_argument("--objective", default=None)
    con.add_argument("--criteria", default=None)
    con.add_argument("--autonomous", action="store_true")

    args = parser.parse_args(argv)
    db = db_connect(args.db)
    try:
        pi = _pi()
        pi.migrate(db)
        criteria = None
        if getattr(args, "criteria", None):
            criteria = [x.strip() for x in args.criteria.split(";") if x.strip()]
        if args.cmd == "register":
            result = register_project(
                db,
                project_key=args.project_key,
                github=args.github,
                clone=not args.no_clone,
                workspace_root=args.workspace,
                config_path=args.config,
            )
        elif args.cmd == "assign":
            result = assign_goal(
                db,
                args.project_key,
                args.goal,
                expand=args.expand,
                config_path=args.config,
                objective=args.objective,
                criteria=criteria,
                autonomous=args.autonomous,
            )
        else:
            result = connect_project(
                db,
                description=args.goal,
                project_key=args.project_key,
                github=args.github,
                clone=not args.no_clone,
                expand=args.expand,
                workspace_root=args.workspace,
                config_path=args.config,
                objective=args.objective,
                criteria=criteria,
                autonomous=args.autonomous,
            )
        print(json.dumps(result, indent=2, default=str))
        return 0
    except (PermissionError, ValueError, FileExistsError, FileNotFoundError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
