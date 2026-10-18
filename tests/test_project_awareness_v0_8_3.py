#!/usr/bin/env python3
"""V0.8.3: project dossier merges scan + memory + open counts."""

import importlib.util
import os
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PI_MOD = REPO / "bin" / "anu-project-intelligence-v0.1.py"
MEM_MOD = REPO / "bin" / "anu-observation-memory-v0.1.py"
REP_MOD = REPO / "bin" / "anu-activity-report-v0.1.py"
LLM_MOD = REPO / "bin" / "anu-llm-planner-v0.1.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_env():
    root = Path(tempfile.mkdtemp())
    workspace = root / "workspace" / "projects"
    cfg_dir = root / "config"
    workspace.mkdir(parents=True)
    cfg_dir.mkdir(parents=True)
    proj = workspace / "sandbox-test-project"
    proj.mkdir()
    (proj / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (proj / "test_app.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
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


def _patch_pi(m, root, workspace, yaml_path, db_path):
    m.BASE = root
    m.PROJECTS_YAML = yaml_path
    m.WORKSPACE = workspace
    m.DB_FILE = db_path
    m._REPO = REPO


def test_dossier_merges_scan_memory_counts():
    pi = load(PI_MOD, "pi_v083")
    mem = load(MEM_MOD, "mem_v083")
    root, workspace, yaml_path, db_path, db = make_env()
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(root)
        _patch_pi(pi, root, workspace, yaml_path, db_path)
        pi.migrate(db)
        pi.sync_registered_projects(db, pi.load_projects_config(yaml_path))
        mem.migrate(db)
        mem.record_verification_outcome(
            db,
            project_key="sandbox-test-project",
            decision="ACCEPT",
            operation="python_test",
            recovery_action="none",
        )
        db.execute(
            "INSERT INTO goals (project_id, description, status) "
            "VALUES (1, 'open goal', 'active')"
        )
        db.execute(
            "INSERT INTO tasks (goal_id, title, status) "
            "VALUES (1, 't1', 'pending')"
        )
        db.commit()

        dossier = pi.build_project_dossier(
            db,
            "sandbox-test-project",
            refresh=True,
            workspace_root=workspace,
        )
        assert dossier["registered"] is True
        assert dossier["has_tests"] is True
        assert dossier["open_goals"] >= 1
        assert dossier["open_tasks"] >= 1
        assert 1 in dossier["recent_goal_ids"]
        assert dossier["verification_hint_count"] >= 1
        assert any(
            "verification" in h for h in dossier["memory_hints"]
        ), dossier["memory_hints"]
        assert "memory_hints" in dossier
        assert "model_hints" in dossier

        ctx = pi.planner_project_context(
            db,
            "sandbox-test-project",
            refresh=False,
            workspace_root=workspace,
        )
        assert "memory_hints" in ctx
        assert ctx["open_goals"] >= 1
        print("PASS dossier merges scan + memory + counts")
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
        db.close()


def test_unregistered_fails():
    pi = load(PI_MOD, "pi_v083_deny")
    root, workspace, yaml_path, db_path, db = make_env()
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(root)
        _patch_pi(pi, root, workspace, yaml_path, db_path)
        pi.migrate(db)
        pi.sync_registered_projects(db, pi.load_projects_config(yaml_path))
        try:
            pi.build_project_dossier(db, "no-such-project", refresh=False)
            raise AssertionError("unregistered should fail")
        except pi.ProjectRegistrationError:
            pass
        print("PASS unregistered dossier fails closed")
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
        db.close()


def test_activity_project_report():
    pi = load(PI_MOD, "pi_v083_rep")
    rep = load(REP_MOD, "rep_v083")
    root, workspace, yaml_path, db_path, db = make_env()
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(root)
        _patch_pi(pi, root, workspace, yaml_path, db_path)
        rep.BASE = root
        pi.migrate(db)
        pi.sync_registered_projects(db, pi.load_projects_config(yaml_path))
        pi.refresh_model(
            db, "sandbox-test-project", workspace_root=workspace
        )
        db.execute(
            "INSERT INTO goals (project_id, description, status) "
            "VALUES (1, 'report goal', 'active')"
        )
        db.commit()

        result = rep.report_project(db, "sandbox-test-project")
        assert result["ok"] is True, result
        assert result["dossier"]["project_key"] == "sandbox-test-project"
        assert "summary" in result
        print("PASS activity project report")
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
        db.close()


def test_llm_prompt_includes_dossier_fields():
    llm = load(LLM_MOD, "llm_v083")
    prompt = llm._build_user_prompt(
        {"description": "do work"},
        "sandbox-test-project",
        {
            "file_count": 3,
            "has_tests": True,
            "known_problem_count": 0,
            "memory_hints": ["verification ACCEPT: python_test"],
            "open_goals": 2,
            "open_tasks": 4,
            "verification_hint_count": 1,
            "memory_repeated_failure": False,
        },
        {"hints": [], "repeated_failure": False},
    )
    assert "project_memory_hints" in prompt
    assert "open_goals" in prompt
    assert "verification_hint_count" in prompt
    print("PASS LLM prompt includes dossier fields")


def main():
    test_dossier_merges_scan_memory_counts()
    test_unregistered_fails()
    test_activity_project_report()
    test_llm_prompt_includes_dossier_fields()
    print("ALL V0.8.3 project awareness tests PASS")


if __name__ == "__main__":
    main()
