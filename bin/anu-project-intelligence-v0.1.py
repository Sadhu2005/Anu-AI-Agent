#!/usr/bin/env python3
"""ANU Project Intelligence v0.1 (V0.6.4).

Single registration path (YAML allow-list + Brain ``projects`` row) and a
read-only scanner that refreshes a structured project model for the planner.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"
PROJECTS_YAML = BASE / "config" / "anu-projects.yaml"
WORKSPACE = BASE / "workspace" / "projects"

MAX_TOP_LEVEL = 40
MAX_TEST_PATHS = 30
MAX_DEP_LINES = 40


class ProjectRegistrationError(PermissionError):
    """Raised when a project is not explicitly registered."""


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


def _load(name, module_name):
    path = _module_path(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def db_connect(db_path=None):
    return _load("anu-db-v0.1.py", "anu_db_v0_1_pi").db_connect(
        db_path if db_path is not None else DB_FILE
    )


def _require_db(db):
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")
    return db


def migrate(db):
    db = _require_db(db)
    for base in (_REPO, BASE):
        sql_path = base / "migrations" / "0007_v0_6_4.sql"
        if sql_path.is_file():
            break
    else:
        raise FileNotFoundError("migrations/0007_v0_6_4.sql")
    for stmt in _split_sql(sql_path.read_text(encoding="utf-8")):
        try:
            db.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "already exists" in str(exc).lower():
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


def _default_yaml_path():
    if PROJECTS_YAML.is_file():
        return PROJECTS_YAML
    repo = _REPO / "config" / "anu-projects.yaml"
    if repo.is_file():
        return repo
    return PROJECTS_YAML


def load_projects_config(path=None):
    path = Path(path) if path else _default_yaml_path()
    if not path.is_file():
        return {
            "projects": [],
            "rules": {"require_explicit_registration": True},
            "path": str(path),
        }
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"projects config root must be a mapping: {path}")
    projects = data.get("projects") or []
    if not isinstance(projects, list):
        raise ValueError("projects must be a list")
    rules = data.get("rules") or {}
    if not isinstance(rules, dict):
        raise ValueError("rules must be a mapping")
    return {"projects": projects, "rules": rules, "path": str(path)}


def _entry_key(entry):
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        key = entry.get("key") or entry.get("project_key") or entry.get("name")
        if isinstance(key, str):
            return key.strip()
    raise ValueError(f"invalid project entry: {entry!r}")


def registered_keys(config=None):
    config = config or load_projects_config()
    keys = []
    for entry in config["projects"]:
        keys.append(_entry_key(entry))
    return keys


def add_yaml_project(
    project_key,
    *,
    name=None,
    repository="local",
    config_path=None,
):
    """V0.8.13: append a project to ``anu-projects.yaml`` if missing."""
    paths = _load("anu-project-paths-v0.1.py", "anu_project_paths_pi")
    key = paths.validate_project_name(project_key)
    path = Path(config_path) if config_path else _default_yaml_path()
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"projects config root must be a mapping: {path}")
    projects = data.get("projects")
    if projects is None:
        projects = []
        data["projects"] = projects
    if not isinstance(projects, list):
        raise ValueError("projects must be a list")
    existing = {_entry_key(entry) for entry in projects}
    added = key not in existing
    if added:
        projects.append(
            {
                "key": key,
                "name": name or key,
                "repository": repository or "local",
            }
        )
        if "rules" not in data or not isinstance(data.get("rules"), dict):
            data["rules"] = {"require_explicit_registration": True}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )
        except OSError as exc:
            # Dashboard runs with ReadOnlyPaths=/srv/anu/config — cannot mutate YAML.
            # Operator must pre-register projects (or apply config via install scripts).
            errno = getattr(exc, "errno", None)
            if errno in (30, 13):  # EROFS, EACCES
                raise PermissionError(
                    f"cannot register new project {key!r}: config is read-only "
                    f"({path}). Add it to anu-projects.yaml on the host, then retry."
                ) from exc
            raise
    return {
        "ok": True,
        "project_key": key,
        "added": added,
        "path": str(path),
        "repository": repository or "local",
    }


def require_registered(project_key, config=None):
    """Fail closed when explicit registration is required and key is absent."""
    paths = _load("anu-project-paths-v0.1.py", "anu_project_paths_pi")
    key = paths.validate_project_name(project_key)
    config = config or load_projects_config()
    rules = config.get("rules") or {}
    if not rules.get("require_explicit_registration", True):
        return key
    allowed = set(registered_keys(config))
    if key not in allowed:
        raise ProjectRegistrationError(
            f"project not registered: {key!r}; "
            f"add it to anu-projects.yaml before mutation/planning"
        )
    return key


def production_deployment_requires_approval(config=None):
    """V0.6.7: whether production-impacting actions need a human approval."""
    config = config or load_projects_config()
    rules = config.get("rules") or {}
    return bool(rules.get("production_deployment_requires_approval", True))


def gate_production_deployment(db, *, task_id=None, goal_id=None, proposed_change=None):
    """Raise or return approval wait when production deploy is attempted."""
    if not production_deployment_requires_approval():
        return {"classification": "AUTONOMOUS", "approval": None}
    approval = _load(
        "anu-approval-service-v0.1.py",
        "anu_approval_service_v0_1_pi",
    )
    approval.migrate(db)
    return approval.require_autonomous_or_approved(
        db,
        action="production_deployment",
        task_id=task_id,
        goal_id=goal_id,
        production_impacting=True,
        proposed_change=proposed_change,
    )


def get_project(db, project_key):
    db = _require_db(db)
    paths = _load("anu-project-paths-v0.1.py", "anu_project_paths_pi")
    key = paths.validate_project_name(project_key)
    row = db.execute(
        """SELECT id, project_key, name, repository, workspace, status, created_at
           FROM projects WHERE project_key = ?""",
        (key,),
    ).fetchone()
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    return row


def ensure_project_row(
    db,
    project_key,
    *,
    name=None,
    repository="local",
    workspace=None,
    status="active",
    config=None,
):
    """Insert Brain projects row if missing (registration must already pass)."""
    db = _require_db(db)
    key = require_registered(project_key, config)
    existing = get_project(db, key)
    if existing is not None:
        return existing
    workspace = workspace or str(WORKSPACE / key)
    name = name or key
    cur = db.execute(
        """INSERT INTO projects(project_key, name, repository, workspace, status)
           VALUES (?, ?, ?, ?, ?)""",
        (key, name, repository, workspace, status),
    )
    db.commit()
    return get_project(db, key) or {
        "id": cur.lastrowid,
        "project_key": key,
        "name": name,
        "repository": repository,
        "workspace": workspace,
        "status": status,
    }


def sync_registered_projects(db, config=None):
    """Ensure every YAML-registered key has a Brain projects row."""
    db = _require_db(db)
    migrate(db)
    config = config or load_projects_config()
    created = []
    for entry in config["projects"]:
        if isinstance(entry, str):
            key = entry.strip()
            meta = {}
        else:
            key = _entry_key(entry)
            meta = entry
        row = ensure_project_row(
            db,
            key,
            name=meta.get("name"),
            repository=meta.get("repository", "local"),
            workspace=meta.get("workspace"),
        )
        created.append(row["project_key"])
    return {"ok": True, "synced": created}


def _workspace_root():
    live = WORKSPACE
    if live.is_dir():
        return live
    repo = _REPO / "workspace" / "projects"
    return repo if repo.is_dir() else live


def _scan_filesystem(project_dir: Path):
    top = []
    test_paths = []
    py_deps = []
    node_deps = []
    file_count = 0
    has_tests = False

    try:
        for child in sorted(project_dir.iterdir()):
            if child.name.startswith("."):
                continue
            top.append(child.name + ("/" if child.is_dir() else ""))
            if len(top) >= MAX_TOP_LEVEL:
                break
    except OSError:
        top = []

    for dirpath, dirnames, filenames in os.walk(project_dir):
        # Skip VCS and heavy trees.
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {".git", "__pycache__", "node_modules", ".venv", "venv"}
        ]
        rel_dir = Path(dirpath).relative_to(project_dir)
        for name in filenames:
            file_count += 1
            rel = str(rel_dir / name).replace("\\", "/")
            lower = name.lower()
            if (
                lower.startswith("test_")
                or lower.endswith("_test.py")
                or name == "tests.py"
                or "test" in rel.split("/")[0].lower()
            ):
                has_tests = True
                if len(test_paths) < MAX_TEST_PATHS:
                    test_paths.append(rel)
            if name in ("requirements.txt", "requirements-dev.txt") and len(py_deps) < MAX_DEP_LINES:
                try:
                    lines = (Path(dirpath) / name).read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            py_deps.append(line)
                            if len(py_deps) >= MAX_DEP_LINES:
                                break
                except OSError:
                    pass
            if name == "package.json" and not node_deps:
                try:
                    data = json.loads(
                        (Path(dirpath) / name).read_text(encoding="utf-8")
                    )
                    deps = {}
                    deps.update(data.get("dependencies") or {})
                    deps.update(data.get("devDependencies") or {})
                    node_deps = sorted(deps.keys())[:MAX_DEP_LINES]
                except (OSError, json.JSONDecodeError):
                    pass

    return {
        "files": {
            "count": file_count,
            "top_level": top,
            "has_tests": has_tests,
        },
        "tests": {"paths": test_paths},
        "dependencies": {
            "python": py_deps,
            "node": node_deps,
        },
    }


def _history_from_db(db, project_id):
    goals = db.execute(
        "SELECT COUNT(*) FROM goals WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0]
    tasks = db.execute(
        """SELECT COUNT(*) FROM tasks t
           JOIN goals g ON g.id = t.goal_id
           WHERE g.project_id = ?""",
        (project_id,),
    ).fetchone()[0]
    return {"goal_count": int(goals), "task_count": int(tasks)}


def _known_problems(db, project_key):
    try:
        mem = _load(
            "anu-observation-memory-v0.1.py",
            "anu_obs_mem_pi",
        )
        if hasattr(mem, "migrate"):
            mem.migrate(db)
        pack = mem.retrieve_for_planning(db, project_key=project_key, limit=10)
        problems = [
            {
                "summary": m["summary"],
                "failure_class": m.get("failure_class"),
                "capability": m.get("capability"),
                "observation_id": m.get("observation_id"),
                "confidence": m.get("confidence"),
            }
            for m in pack.get("memories") or []
            if m.get("failure_class")
            or "failure" in (m.get("tags") or [])
            or "denied" in (m.get("tags") or [])
        ]
        return {
            "repeated_failure": bool(pack.get("repeated_failure")),
            "items": problems,
            "hints": pack.get("hints") or [],
        }
    except Exception:
        return {"repeated_failure": False, "items": [], "hints": []}


def scan_project(db, project_key, *, workspace_root=None):
    """Build a full project model (read-only filesystem + Brain + memory)."""
    db = _require_db(db)
    migrate(db)
    key = require_registered(project_key)
    row = ensure_project_row(db, key)
    root = Path(workspace_root) if workspace_root else _workspace_root()
    paths = _load("anu-project-paths-v0.1.py", "anu_project_paths_pi")
    project_dir = paths.resolve_project(root, key)
    fs = _scan_filesystem(project_dir)
    model = {
        "identity": {
            "project_id": row["id"],
            "project_key": row["project_key"],
            "name": row["name"],
            "repository": row["repository"],
            "workspace": str(project_dir),
            "status": row["status"],
        },
        "purpose": row.get("name") or key,
        "files": fs["files"],
        "tests": fs["tests"],
        "dependencies": fs["dependencies"],
        "current_state": {
            "exists": True,
            "path": str(project_dir),
        },
        "history": _history_from_db(db, row["id"]),
        "known_problems": _known_problems(db, key),
        "refreshed_at": now_iso(),
    }
    return model


def save_model(db, project_key, model):
    db = _require_db(db)
    row = get_project(db, project_key)
    if row is None:
        raise ValueError(f"project not in Brain: {project_key}")
    refreshed = model.get("refreshed_at") or now_iso()
    body = json.dumps(model, sort_keys=True)
    db.execute(
        """INSERT INTO project_models(project_id, model_json, refreshed_at)
           VALUES (?, ?, ?)
           ON CONFLICT(project_id) DO UPDATE SET
             model_json = excluded.model_json,
             refreshed_at = excluded.refreshed_at""",
        (row["id"], body, refreshed),
    )
    db.commit()
    return {"project_id": row["id"], "refreshed_at": refreshed}


def refresh_model(db, project_key, *, workspace_root=None):
    model = scan_project(db, project_key, workspace_root=workspace_root)
    save_model(db, project_key, model)
    return model


def get_model(db, project_key):
    db = _require_db(db)
    row = get_project(db, project_key)
    if row is None:
        return None
    stored = db.execute(
        "SELECT model_json, refreshed_at FROM project_models WHERE project_id = ?",
        (row["id"],),
    ).fetchone()
    if stored is None:
        return None
    raw = stored[0] if not hasattr(stored, "keys") else stored["model_json"]
    try:
        model = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return model


OPEN_GOAL_STATUSES = frozenset({"pending", "active"})
OPEN_TASK_STATUSES = frozenset({
    "pending",
    "ready",
    "planning",
    "running",
    "verifying",
    "failed",
})


def _memory_pack_best_effort(db, project_key, *, limit=10):
    """V0.8.3: retrieve_for_planning without failing the dossier."""
    empty = {
        "hints": [],
        "repeated_failure": False,
        "failure_classes": [],
        "memories": [],
    }
    try:
        mem = _load(
            "anu-observation-memory-v0.1.py",
            "anu_obs_mem_dossier",
        )
        if hasattr(mem, "migrate"):
            mem.migrate(db)
        pack = mem.retrieve_for_planning(
            db, project_key=project_key, limit=limit
        )
        if not isinstance(pack, dict):
            return empty
        return {
            "hints": list(pack.get("hints") or []),
            "repeated_failure": bool(pack.get("repeated_failure")),
            "failure_classes": list(pack.get("failure_classes") or []),
            "memories": list(pack.get("memories") or []),
        }
    except Exception:
        return empty


def _open_counts_for_project(db, project_id):
    """Count open goals/tasks and recent goal ids for a Brain project row."""
    if project_id is None:
        return {"open_goals": 0, "open_tasks": 0, "recent_goal_ids": []}
    goal_rows = db.execute(
        """
        SELECT id, status FROM goals
        WHERE project_id = ?
        ORDER BY id DESC
        """,
        (project_id,),
    ).fetchall()
    recent_goal_ids = []
    open_goals = 0
    for row in goal_rows:
        gid = row[0] if not isinstance(row, sqlite3.Row) else row["id"]
        status = row[1] if not isinstance(row, sqlite3.Row) else row["status"]
        if len(recent_goal_ids) < 5:
            recent_goal_ids.append(int(gid))
        if status in OPEN_GOAL_STATUSES:
            open_goals += 1
    placeholders = ",".join("?" for _ in OPEN_TASK_STATUSES) or "NULL"
    open_tasks = db.execute(
        f"""
        SELECT COUNT(*) FROM tasks t
        JOIN goals g ON g.id = t.goal_id
        WHERE g.project_id = ?
          AND t.status IN ({placeholders})
        """,
        (project_id, *sorted(OPEN_TASK_STATUSES)),
    ).fetchone()[0]
    return {
        "open_goals": int(open_goals),
        "open_tasks": int(open_tasks),
        "recent_goal_ids": recent_goal_ids,
    }


def build_project_dossier(
    db,
    project_key,
    *,
    refresh=True,
    workspace_root=None,
    memory_limit=10,
):
    """V0.8.3: scan model + memory hints + open goal/task counts."""
    db = _require_db(db)
    key = require_registered(project_key)
    if refresh:
        model = refresh_model(db, key, workspace_root=workspace_root)
    else:
        model = get_model(db, key) or refresh_model(
            db, key, workspace_root=workspace_root
        )
    problems = (model.get("known_problems") or {}).get("items") or []
    model_hints = [
        p.get("summary")
        for p in problems
        if isinstance(p, dict) and p.get("summary")
    ]
    # Prefer live memory pack; fall back to scan-embedded hints.
    mem = _memory_pack_best_effort(db, key, limit=memory_limit)
    memory_hints = mem["hints"] or list(
        (model.get("known_problems") or {}).get("hints") or []
    )
    verification_hint_count = sum(
        1 for h in memory_hints if isinstance(h, str) and "verification" in h
    )
    row = get_project(db, key)
    project_id = None if row is None else row["id"]
    counts = _open_counts_for_project(db, project_id)
    return {
        "project_key": key,
        "registered": True,
        "model_refreshed_at": model.get("refreshed_at"),
        "file_count": (model.get("files") or {}).get("count"),
        "has_tests": (model.get("files") or {}).get("has_tests"),
        "known_problem_count": len(problems),
        "repeated_failure": bool(
            mem["repeated_failure"]
            or (model.get("known_problems") or {}).get("repeated_failure")
        ),
        "top_level": (model.get("files") or {}).get("top_level") or [],
        # Backward-compatible alias used by older planners.
        "hints": memory_hints,
        "model_hints": model_hints,
        "memory_hints": memory_hints,
        "memory_repeated_failure": bool(mem["repeated_failure"]),
        "memory_failure_classes": list(mem["failure_classes"]),
        "verification_hint_count": int(verification_hint_count),
        "open_goals": counts["open_goals"],
        "open_tasks": counts["open_tasks"],
        "recent_goal_ids": counts["recent_goal_ids"],
    }


def planner_project_context(db, project_key, *, refresh=True, workspace_root=None):
    """Context block for template expand / planning (dossier-backed)."""
    return build_project_dossier(
        db,
        project_key,
        refresh=refresh,
        workspace_root=workspace_root,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Project Intelligence v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate")
    sub.add_parser("list-registered")
    sync_p = sub.add_parser("sync")
    req = sub.add_parser("require")
    req.add_argument("--project", required=True)
    scan_p = sub.add_parser("scan")
    scan_p.add_argument("--project", required=True)
    scan_p.add_argument("--refresh", action="store_true")
    get_p = sub.add_parser("get-model")
    get_p.add_argument("--project", required=True)
    dossier_p = sub.add_parser("dossier")
    dossier_p.add_argument("--project", required=True)
    dossier_p.add_argument(
        "--no-refresh",
        action="store_true",
        help="Use stored model when present (still scans if missing)",
    )

    args = parser.parse_args(argv)
    config = load_projects_config(args.config)

    if args.cmd == "list-registered":
        print(
            json.dumps(
                {
                    "ok": True,
                    "config": config["path"],
                    "projects": registered_keys(config),
                    "rules": config["rules"],
                },
                indent=2,
            )
        )
        return 0

    db = db_connect(args.db)
    try:
        migrate(db)
        if args.cmd == "migrate":
            print(json.dumps({"ok": True, "migrated": True}))
            return 0
        if args.cmd == "sync":
            result = sync_registered_projects(db, config)
            print(json.dumps(result, indent=2))
            return 0
        if args.cmd == "require":
            key = require_registered(args.project, config)
            print(json.dumps({"ok": True, "project": key}))
            return 0
        if args.cmd == "scan":
            if args.refresh:
                model = refresh_model(db, args.project)
            else:
                model = scan_project(db, args.project)
            print(json.dumps({"ok": True, "model": model}, indent=2))
            return 0
        if args.cmd == "get-model":
            model = get_model(db, args.project)
            if model is None:
                print(json.dumps({"ok": False, "error": "no_model"}))
                return 1
            print(json.dumps({"ok": True, "model": model}, indent=2))
            return 0
        if args.cmd == "dossier":
            dossier = build_project_dossier(
                db,
                args.project,
                refresh=not args.no_refresh,
            )
            print(json.dumps({"ok": True, "dossier": dossier}, indent=2))
            return 0
        parser.error(f"unknown command: {args.cmd}")
        return 2
    except ProjectRegistrationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
