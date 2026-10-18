#!/usr/bin/env python3
"""V0.6.4: project registration, scan model, unregistered deny."""

import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-project-intelligence-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("proj_intel_t", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_env():
    root = Path(tempfile.mkdtemp())
    workspace = root / "workspace" / "projects"
    cfg_dir = root / "config"
    workspace.mkdir(parents=True)
    cfg_dir.mkdir(parents=True)
    (workspace / "sandbox-test-project").mkdir()
    (workspace / "sandbox-test-project" / "app.py").write_text(
        "print('hi')\n", encoding="utf-8"
    )
    (workspace / "sandbox-test-project" / "test_app.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (workspace / "sandbox-test-project" / "requirements.txt").write_text(
        "pytest\n", encoding="utf-8"
    )
    (workspace / "unregistered-proj").mkdir()
    yaml_path = cfg_dir / "anu-projects.yaml"
    yaml_path.write_text(
        """
projects:
  - key: sandbox-test-project
    name: Sandbox
    repository: local
rules:
  require_explicit_registration: true
""",
        encoding="utf-8",
    )
    db_path = root / "brain" / "anu-brain.db"
    db_path.parent.mkdir(parents=True)
    db = sqlite3.connect(str(db_path))
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            observation_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.commit()
    return root, workspace, yaml_path, db_path, db


def test_registration_and_unregistered_deny():
    m = load()
    root, workspace, yaml_path, db_path, db = make_env()
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(root)
        # Reload module paths by patching.
        m.BASE = root
        m.PROJECTS_YAML = yaml_path
        m.WORKSPACE = workspace
        m.DB_FILE = db_path

        cfg = m.load_projects_config(yaml_path)
        assert "sandbox-test-project" in m.registered_keys(cfg)
        assert m.require_registered("sandbox-test-project", cfg) == "sandbox-test-project"
        try:
            m.require_registered("unregistered-proj", cfg)
            raise AssertionError("unregistered should deny")
        except m.ProjectRegistrationError:
            pass
        print("PASS registration allow/deny")
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
        db.close()


def test_scan_refresh_and_get_model():
    m = load()
    root, workspace, yaml_path, db_path, db = make_env()
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(root)
        m.BASE = root
        m.PROJECTS_YAML = yaml_path
        m.WORKSPACE = workspace
        m.DB_FILE = db_path
        m._REPO = REPO

        m.migrate(db)
        m.sync_registered_projects(db, m.load_projects_config(yaml_path))
        model = m.refresh_model(
            db, "sandbox-test-project", workspace_root=workspace
        )
        assert model["identity"]["project_key"] == "sandbox-test-project"
        assert model["files"]["count"] >= 2
        assert model["files"]["has_tests"] is True
        assert "pytest" in model["dependencies"]["python"]
        stored = m.get_model(db, "sandbox-test-project")
        assert stored is not None
        assert stored["refreshed_at"]
        ctx = m.planner_project_context(
            db, "sandbox-test-project", refresh=False, workspace_root=workspace
        )
        assert ctx["registered"] is True
        assert ctx["has_tests"] is True
        print("PASS scan/refresh/get model + planner context")
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
        db.close()


if __name__ == "__main__":
    print("===== ANU V0.6.4 PROJECT INTELLIGENCE =====")
    test_registration_and_unregistered_deny()
    test_scan_refresh_and_get_model()
    print("PROJECT_INTELLIGENCE_TEST_COMPLETE")
