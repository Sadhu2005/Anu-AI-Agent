#!/usr/bin/env python3
"""V0.8.7: multi_stage research/github ops wired into planning + task engine."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
TPL = REPO / "bin" / "anu-template-planner-v0.1.py"
ENG = REPO / "bin" / "anu-task-engine-v0.5.3.py"
APPROVAL = REPO / "bin" / "anu-approval-service-v0.1.py"
GATEWAY = REPO / "config" / "anu-command-gateway.yaml"
PROFILES = REPO / "config" / "anu-execution-profiles.yaml"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_planner_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
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
            parent_task_id INTEGER,
            task_key TEXT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 5,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(goal_id) REFERENCES goals(id)
        );
        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, depends_on_task_id),
            CHECK(task_id != depends_on_task_id)
        );
        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        CREATE TABLE agent_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            plan_id TEXT,
            plan_json TEXT,
            status TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        "INSERT INTO goals (description, status) VALUES (?, ?)",
        ("Improve sandbox-test-project", "pending"),
    )
    db.commit()
    return path, db


def test_template_research_uses_research_operation():
    planner = load(TPL, "tpl_087")
    planner.BASE = REPO
    assert "research" in planner.ALLOWED_OPERATIONS
    assert "github" in planner.ALLOWED_OPERATIONS
    plan = planner.build_plan_dict(1, "multi_stage_goal", plan_id="x")
    research = plan["actions"][0]
    assert research["parameters"]["role"] == "research"
    assert research["parameters"]["operation"] == "research"
    print("PASS template research uses research operation")


def test_expand_persists_research_spec():
    planner = load(TPL, "tpl_087b")
    planner.BASE = REPO
    path, db = make_planner_db()
    try:
        result = planner.expand_goal(
            db,
            goal_id=1,
            project="sandbox-test-project",
            changes_file="changes.json",
            plan_id="plan-087",
        )
        research_task = result["tasks"][0]
        assert research_task["role"] == "research"
        assert research_task["operation"] == "research"
        spec = db.execute(
            "SELECT operation FROM task_execution_specs WHERE task_id = ?",
            (research_task["task_id"],),
        ).fetchone()
        assert spec["operation"] == "research"
        print("PASS expand persists research spec")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def test_build_capability_invoke_args():
    eng = load(ENG, "eng_087")
    path, db = make_planner_db()
    try:
        db.execute(
            "INSERT INTO tasks (goal_id, title, description, status) "
            "VALUES (1, 'multi_stage_goal:research', 'survey', 'pending')"
        )
        db.commit()
        args = eng.build_capability_invoke_args(
            db, operation="research", project="sandbox-test-project", task_id=1
        )
        assert args["action"] == "search"
        assert "Improve sandbox" in args["query"]
        gh = eng.build_capability_invoke_args(
            db, operation="github", project="sandbox-test-project", task_id=1
        )
        assert gh["action"] == "repo"
        assert gh["owner"] == "Sadhu2005"
        assert gh["repo"] == "Anu-AI-Agent"
        print("PASS build_capability_invoke_args")
    finally:
        db.close()
        path.unlink(missing_ok=True)


def test_capability_path_requests_approval_then_completes():
    eng = load(ENG, "eng_087c")
    appr = load(APPROVAL, "appr_087")
    root = Path(tempfile.mkdtemp())
    db_path = root / "anu-brain.db"
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            parent_task_id INTEGER,
            task_key TEXT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 5,
            attempts INTEGER NOT NULL DEFAULT 0,
            failure_class TEXT,
            retry_after TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        CREATE TABLE research (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            url TEXT,
            title TEXT,
            summary TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        "INSERT INTO goals (description) VALUES ('Learn about Raspberry Pi')"
    )
    db.execute(
        "INSERT INTO tasks (goal_id, title, status) VALUES (1, 'research', 'pending')"
    )
    db.commit()
    db.close()

    eng.DB_FILE = db_path
    eng.BASE = REPO
    eng._HERE = REPO / "bin"

    def fake_db_connect(db_path=None):
        conn = sqlite3.connect(str(eng.DB_FILE))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    with mock.patch.object(eng, "db_connect", side_effect=fake_db_connect):
        with mock.patch.object(
            eng,
            "enforce_dependency_admission",
            return_value={"state": "READY", "reason": "no_deps"},
        ):
          with mock.patch.object(
              eng, "shared_execution_lock", return_value=contextlib.nullcontext()
          ):
            # First run: approval required
            payload, code = eng.start_task(
                1, "sandbox-test-project", "changes.json", "research"
            )
            assert code == 2, (payload, code)
            assert payload["reason"] == "approval_required"
            assert payload["task_status"] == "waiting"

            # Approve
            db = fake_db_connect()
            appr.migrate(db)
            row = db.execute(
                "SELECT id FROM approvals WHERE task_id = 1 ORDER BY id DESC"
            ).fetchone()
            appr.approve(db, row["id"], decided_by="tester")
            db.execute("UPDATE tasks SET status = 'pending' WHERE id = 1")
            db.commit()
            db.close()

            fake_out = {
                "ok": True,
                "action": "search",
                "title": "Raspberry Pi",
                "research_id": 9,
            }

            def fake_run(argv, capture_output=True, text=True, check=False):
                class P:
                    returncode = 0
                    stdout = json.dumps(fake_out)
                    stderr = ""

                assert "anu-research-v0.1.py" in " ".join(str(a) for a in argv)
                return P()

            with mock.patch("subprocess.run", side_effect=fake_run):
                payload2, code2 = eng.start_task(
                    1, "sandbox-test-project", "changes.json", "research"
                )
            assert code2 == 0, (payload2, code2)
            assert payload2["ok"] is True
            assert payload2["task_status"] == "completed"
    print("PASS capability path approval then complete")


def main():
    test_template_research_uses_research_operation()
    test_expand_persists_research_spec()
    test_build_capability_invoke_args()
    test_capability_path_requests_approval_then_completes()
    print("ALL_PASS test_planning_research_wire_v0_8_7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
