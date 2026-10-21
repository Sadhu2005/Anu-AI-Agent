#!/usr/bin/env python3
"""V0.8.13: register allow-listed repo + assign a goal."""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ASSIGN = REPO / "bin" / "anu-project-assign-v0.1.py"
PI = REPO / "bin" / "anu-project-intelligence-v0.1.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            repository TEXT NOT NULL,
            workspace TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id)
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            goal_id INTEGER,
            parent_task_id INTEGER,
            run_id TEXT,
            attempt INTEGER,
            event_type TEXT,
            message TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.commit()
    return path, db


def test_parse_github_spec():
    asg = load(ASSIGN, "asg_0813a")
    assert asg.parse_github_spec("Sadhu2005/todo-app") == ("Sadhu2005", "todo-app")
    assert asg.parse_github_spec(
        "https://github.com/Sadhu2005/todo-app.git"
    ) == ("Sadhu2005", "todo-app")
    assert asg.parse_github_spec("Sadhu2005/a nu-agent-test") == (
        "Sadhu2005",
        "anu-agent-test",
    )
    assert asg.parse_github_spec("repo: Sadhu2005/anu-agent-test") == (
        "Sadhu2005",
        "anu-agent-test",
    )
    try:
        asg.parse_github_spec("git@github.com:Sadhu2005/todo-app.git")
    except PermissionError:
        pass
    else:
        raise AssertionError("ssh should be denied")
    try:
        asg.parse_github_spec("https://evil.example/Sadhu2005/todo-app")
    except PermissionError:
        pass
    else:
        raise AssertionError("non-github host should be denied")
    print("PASS parse_github_spec")


def test_reject_non_allowlisted_owner():
    asg = load(ASSIGN, "asg_0813b")
    cfg = {"allow_owners": ["Sadhu2005"]}
    asg.require_allowlisted_owner("Sadhu2005", github_cfg=cfg)
    try:
        asg.require_allowlisted_owner("stranger", github_cfg=cfg)
    except PermissionError as exc:
        assert "allow-listed" in str(exc)
    else:
        raise AssertionError("stranger owner should be denied")
    print("PASS reject non-allowlisted owner")


def test_register_local_and_assign_goal():
    asg = load(ASSIGN, "asg_0813c")
    asg.BASE = REPO
    path, db = make_db()
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            yaml_path = root / "anu-projects.yaml"
            yaml_path.write_text(
                "projects: []\nrules:\n  require_explicit_registration: true\n",
                encoding="utf-8",
            )
            ws = root / "projects"
            ws.mkdir()
            result = asg.connect_project(
                db,
                description="Add a simple todo list",
                project_key="v0813-todo",
                clone=False,
                expand=False,
                workspace_root=ws,
                config_path=yaml_path,
            )
            assert result["ok"] is True
            assert result["register"]["project_key"] == "v0813-todo"
            assert result["register"]["cloned"] is False
            goal = result["assign"]["goal"]
            assert goal["status"] == "pending"
            assert "todo" in goal["description"]
            dest = ws / "v0813-todo"
            assert dest.is_dir()
            text = yaml_path.read_text(encoding="utf-8")
            assert "v0813-todo" in text
            row = db.execute(
                "SELECT project_key, repository FROM projects WHERE project_key=?",
                ("v0813-todo",),
            ).fetchone()
            assert row["repository"] == "local"
    finally:
        db.close()
        path.unlink(missing_ok=True)
    print("PASS register local and assign goal")


def test_github_register_clones_when_empty():
    asg = load(ASSIGN, "asg_0813d")
    asg.BASE = REPO
    path, db = make_db()
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            yaml_path = root / "anu-projects.yaml"
            yaml_path.write_text("projects: []\n", encoding="utf-8")
            ws = root / "projects"
            ws.mkdir()
            calls = []

            class P:
                returncode = 0
                stdout = ""
                stderr = ""

            def fake_run(argv, **kwargs):
                calls.append(argv)
                dest = Path(argv[-1])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "README.md").write_text("ok", encoding="utf-8")
                return P()

            result = asg.register_project(
                db,
                github="Sadhu2005/todo-app",
                clone=True,
                workspace_root=ws,
                config_path=yaml_path,
                runner=fake_run,
                github_cfg={"allow_owners": ["Sadhu2005"]},
            )
            assert result["cloned"] is True
            assert result["repository"] == "https://github.com/Sadhu2005/todo-app.git"
            assert any("clone" in a for a in calls[0])
            assert (ws / "todo-app" / "README.md").is_file()
    finally:
        db.close()
        path.unlink(missing_ok=True)
    print("PASS github register clones when empty")


def test_add_yaml_project_idempotent():
    pi = load(PI, "pi_0813")
    with tempfile.TemporaryDirectory() as td:
        yaml_path = Path(td) / "anu-projects.yaml"
        first = pi.add_yaml_project(
            "demo-a", name="Demo A", repository="local", config_path=yaml_path
        )
        second = pi.add_yaml_project(
            "demo-a", name="Demo A", repository="local", config_path=yaml_path
        )
        assert first["added"] is True
        assert second["added"] is False
        cfg = pi.load_projects_config(yaml_path)
        assert pi.registered_keys(cfg) == ["demo-a"]
    print("PASS add_yaml_project idempotent")


def main():
    test_parse_github_spec()
    test_reject_non_allowlisted_owner()
    test_register_local_and_assign_goal()
    test_github_register_clones_when_empty()
    test_add_yaml_project_idempotent()
    print("ALL_PASS test_project_assign_v0_8_13")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
