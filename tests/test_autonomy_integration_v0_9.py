#!/usr/bin/env python3
"""V0.9 integration: fake 3-stage goal → COMPLETE; pause/resume; fingerprint PAUSE."""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


def _load(name, mod_name):
    path = BIN / name
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY, project_key TEXT UNIQUE,
            name TEXT, repository TEXT, workspace TEXT,
            status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY, project_id INTEGER, description TEXT,
            status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER,
            title TEXT, status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY, task_id INTEGER, depends_on_task_id INTEGER
        );
        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY, project TEXT, changes_file TEXT, operation TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, message TEXT,
            metadata TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, goal_id INTEGER, task_id INTEGER
        );
        """
    )
    db.execute(
        "INSERT INTO projects VALUES (1,'sandbox','s','local','/tmp/s','active',datetime('now'))"
    )
    db.execute("INSERT INTO goals(id, project_id, description) VALUES (1,1,'todo')")
    db.commit()
    return path, db


class TestAutonomyIntegration(unittest.TestCase):
    def setUp(self):
        self.state = _load("anu-autonomy-state-v0.1.py", "anu_state_i")
        self.ctrl = _load("anu-autonomy-controller-v0.1.py", "anu_ctrl_i")
        self.path, self.db = _fresh()
        self.state.migrate(self.db)
        self.state.create_objective(
            self.db, 1, "todo",
            ["implement", "tests"],
            autonomous=True,
        )
        self.cfg = self.ctrl.load_config(ROOT / "config" / "anu-autonomy.yaml")

    def tearDown(self):
        self.db.close()
        self.path.unlink(missing_ok=True)

    def _task(self, status, op="python_test"):
        cur = self.db.execute(
            "INSERT INTO tasks(goal_id, title, status) VALUES (1, ?, ?)",
            (f"t-{status}", status),
        )
        tid = cur.lastrowid
        self.db.execute(
            """INSERT INTO task_execution_specs(task_id, project, changes_file, operation)
               VALUES (?, 'sandbox', '.anu-changes.json', ?)""",
            (tid, op),
        )
        self.db.commit()
        return tid

    def test_three_stage_to_complete(self):
        for _ in range(3):
            self._task("completed")
        for item in self.state.list_checklist(self.db, 1):
            self.state.update_checklist_item(
                self.db, item["id"], status="done", evidence="verified"
            )
        d = self.ctrl.decide(self.db, 1, config=self.cfg)
        self.assertEqual(d["decision"], "COMPLETE")
        applied = self.ctrl.apply_decision(self.db, 1, d, config=self.cfg)
        self.assertTrue(applied["ok"])

    def test_pause_resume(self):
        self._task("pending")
        self.state.set_control(self.db, "pause", scope="goal", goal_id=1, reason="manual")
        d = self.ctrl.decide(self.db, 1, config=self.cfg)
        self.assertEqual(d["decision"], "PAUSE_FOR_USER")
        self.state.set_control(self.db, "run", scope="goal", goal_id=1, reason="resume")
        d2 = self.ctrl.decide(self.db, 1, config=self.cfg)
        self.assertEqual(d2["decision"], "CONTINUE")

    def test_identical_failure_pauses(self):
        self._task("failed")
        self.cfg["limits"]["max_same_fingerprint"] = 2
        self.ctrl.decide(self.db, 1, config=self.cfg)
        self.ctrl.decide(self.db, 1, config=self.cfg)
        d = self.ctrl.decide(self.db, 1, config=self.cfg)
        self.assertEqual(d["decision"], "PAUSE_FOR_USER")
        self.assertEqual(d["reason"], "same_fingerprint_limit")


if __name__ == "__main__":
    unittest.main()
