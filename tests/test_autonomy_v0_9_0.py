#!/usr/bin/env python3
"""V0.9.0 autonomy state + controller unit tests."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


def _load(name, mod_name):
    path = BIN / name
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
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
            parent_task_id INTEGER,
            task_key TEXT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 5,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            UNIQUE(task_id, depends_on_task_id)
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
            event_type TEXT NOT NULL,
            message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            task_id INTEGER, goal_id INTEGER, parent_task_id INTEGER,
            run_id TEXT, attempt INTEGER
        );
        """
    )
    db.execute(
        """INSERT INTO projects(project_key, name, repository, workspace)
           VALUES ('sandbox-test-project', 'sandbox', 'local', '/tmp/sandbox')"""
    )
    db.execute(
        """INSERT INTO goals(project_id, description, status)
           VALUES (1, 'Add a simple todo list', 'pending')"""
    )
    db.commit()
    return path, db


class TestAutonomyState(unittest.TestCase):
    def setUp(self):
        self.state = _load("anu-autonomy-state-v0.1.py", "anu_state_t")
        self.path, self.db = _fresh_db()
        self.state.migrate(self.db)

    def tearDown(self):
        self.db.close()
        self.path.unlink(missing_ok=True)

    def test_migration_idempotent(self):
        a = self.state.migrate(self.db)
        b = self.state.migrate(self.db)
        self.assertTrue(a.get("ok"))
        self.assertTrue(b.get("ok"))

    def test_objective_and_checklist(self):
        obj = self.state.create_objective(
            self.db, 1, "Ship todo list",
            ["add works", "tests pass"],
            autonomous=True,
        )
        self.assertTrue(obj["autonomous"])
        self.assertEqual(len(obj["checklist"]), 2)
        item_id = obj["checklist"][0]["id"]
        with self.assertRaises(ValueError):
            self.state.update_checklist_item(self.db, item_id, status="done", evidence="")
        self.state.update_checklist_item(
            self.db, item_id, status="done", evidence="test_todo.py PASS"
        )
        items = self.state.list_checklist(self.db, 1)
        self.assertEqual(items[0]["status"], "done")

    def test_complete_pending_checklist_bulk(self):
        self.state.create_objective(
            self.db, 2, "Ship", ["a", "b", "c"], autonomous=True
        )
        out = self.state.complete_pending_checklist(
            self.db, 2, "verification_accept=1:tx=mut-x"
        )
        self.assertEqual(out["updated"], 3)
        items = self.state.list_checklist(self.db, 2)
        self.assertTrue(all(i["status"] == "done" for i in items))
        self.assertTrue(all((i["evidence"] or "").startswith("verification") for i in items))

    def test_progress_atomic_write(self):
        self.state.create_objective(self.db, 1, "Ship todo", autonomous=True)
        with tempfile.TemporaryDirectory() as td:
            self.state.WORKSPACE = Path(td)
            path = self.state.write_progress_file(
                self.db, 1, "sandbox-test-project",
                current_task="task-1", elapsed=12,
            )
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn("## Objective", text)
            self.assertIn("## Current task", text)
            self.assertIn("## Done", text)
            self.assertIn("## Failed", text)
            self.assertIn("## Next", text)
            self.assertIn("## Blockers", text)
            self.assertIn("## Elapsed", text)

    def test_control_pause_stop(self):
        self.state.set_control(self.db, "pause", reason="test", by_who="unit")
        ctl = self.state.effective_control(self.db)
        self.assertEqual(ctl["state"], "pause")
        self.state.set_control(self.db, "stop", reason="halt", by_who="unit")
        ctl = self.state.effective_control(self.db)
        self.assertEqual(ctl["state"], "stop")


class TestAutonomyController(unittest.TestCase):
    def setUp(self):
        self.state = _load("anu-autonomy-state-v0.1.py", "anu_state_t2")
        self.ctrl = _load("anu-autonomy-controller-v0.1.py", "anu_ctrl_t")
        self.path, self.db = _fresh_db()
        self.state.migrate(self.db)
        self.state.create_objective(self.db, 1, "Ship todo", autonomous=True)
        self.cfg = self.ctrl.load_config(ROOT / "config" / "anu-autonomy.yaml")

    def tearDown(self):
        self.db.close()
        self.path.unlink(missing_ok=True)

    def _add_task(self, status, operation="python_test", title="t"):
        cur = self.db.execute(
            """INSERT INTO tasks(goal_id, title, status, attempts)
               VALUES (1, ?, ?, 0)""",
            (title, status),
        )
        tid = cur.lastrowid
        self.db.execute(
            """INSERT INTO task_execution_specs(task_id, project, changes_file, operation)
               VALUES (?, 'sandbox-test-project', '.anu-changes.json', ?)""",
            (tid, operation),
        )
        self.db.commit()
        return tid

    def test_decide_pause_on_control(self):
        self.state.set_control(self.db, "pause", scope="goal", goal_id=1, reason="x")
        d = self.ctrl.decide(self.db, 1, config=self.cfg)
        self.assertEqual(d["decision"], "PAUSE_FOR_USER")

    def test_decide_continue_ready(self):
        self._add_task("pending")
        d = self.ctrl.decide(self.db, 1, config=self.cfg)
        self.assertEqual(d["decision"], "CONTINUE")

    def test_decide_complete(self):
        self._add_task("completed")
        # Mark checklist done with evidence
        items = self.state.list_checklist(self.db, 1)
        for item in items:
            self.state.update_checklist_item(
                self.db, item["id"], status="done", evidence="ok"
            )
        d = self.ctrl.decide(self.db, 1, config=self.cfg)
        self.assertEqual(d["decision"], "COMPLETE")

    def test_decide_complete_with_pending_checklist(self):
        self._add_task("completed")
        d = self.ctrl.decide(self.db, 1, config=self.cfg)
        self.assertEqual(d["decision"], "COMPLETE")
        self.assertEqual(d["reason"], "all_tasks_done_checklist_pending")
        applied = self.ctrl.apply_decision(self.db, 1, d, config=self.cfg)
        self.assertEqual(applied["decision"], "COMPLETE")
        items = self.state.list_checklist(self.db, 1)
        self.assertTrue(items)
        self.assertTrue(all(i["status"] == "done" for i in items))
        self.assertTrue(all((i.get("evidence") or "").strip() for i in items))

    def test_fingerprint_limit(self):
        self._add_task("failed", title="boom")
        self.cfg["limits"]["max_same_fingerprint"] = 2
        d1 = self.ctrl.decide(self.db, 1, config=self.cfg)
        self.assertEqual(d1["decision"], "REPLAN")
        d2 = self.ctrl.decide(self.db, 1, config=self.cfg)
        # second identical failure still replan or pause depending on count
        self.assertIn(d2["decision"], ("REPLAN", "PAUSE_FOR_USER"))
        d3 = self.ctrl.decide(self.db, 1, config=self.cfg)
        self.assertEqual(d3["decision"], "PAUSE_FOR_USER")
        self.assertEqual(d3["reason"], "same_fingerprint_limit")

    def test_guardian_wait(self):
        self._add_task("pending")
        d = self.ctrl.decide(
            self.db, 1, config=self.cfg,
            guardian_data={"status": "PAUSE"},
        )
        self.assertEqual(d["decision"], "WAIT")

    def test_fingerprint_helper(self):
        a = self.ctrl.fingerprint_failure("python_test", "TEST_FAILURE", "line 12 failed")
        b = self.ctrl.fingerprint_failure("python_test", "TEST_FAILURE", "line 99 failed")
        self.assertEqual(a, b)  # digits normalized


class TestPlannerSoftwareGoal(unittest.TestCase):
    def setUp(self):
        self.planner = _load("anu-template-planner-v0.1.py", "anu_planner_t")

    def test_software_goal_has_mutation(self):
        steps = self.planner.TEMPLATES["software_goal"]
        self.planner.validate_plan_steps(steps)

    def test_reject_research_only(self):
        with self.assertRaises(ValueError):
            self.planner.validate_plan_steps([
                {"operation": "research"},
                {"operation": "github"},
            ])

    def test_repair_weak_plan(self):
        result = self.planner.repair_weak_plan([{"operation": "research"}])
        self.assertTrue(result["repaired"])
        self.planner.validate_plan_steps(result["steps"])


if __name__ == "__main__":
    unittest.main()
