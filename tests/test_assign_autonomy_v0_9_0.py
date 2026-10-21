#!/usr/bin/env python3
"""V0.9 assign CLI objective/criteria/autonomous flags."""

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


class TestAssignAutonomy(unittest.TestCase):
    def setUp(self):
        self.assign = _load("anu-project-assign-v0.1.py", "anu_assign_t")
        self.state = _load("anu-autonomy-state-v0.1.py", "anu_state_asg")
        self.pi = _load("anu-project-intelligence-v0.1.py", "anu_pi_asg")
        self.goals = _load("anu-goal-service-v0.1.py", "anu_goals_asg")
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = Path(tmp.name)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
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
        self.db.execute(
            """INSERT INTO projects(project_key, name, repository, workspace)
               VALUES ('sandbox-test-project','sandbox','local','/tmp/sandbox')"""
        )
        self.db.commit()
        try:
            self.pi.migrate(self.db)
        except Exception:
            pass
        self.state.migrate(self.db)

    def tearDown(self):
        self.db.close()
        self.path.unlink(missing_ok=True)

    def test_assign_with_objective(self):
        # Bypass require_registered by patching
        with mock.patch.object(self.assign, "_pi", return_value=self.pi):
            with mock.patch.object(self.pi, "require_registered", return_value="sandbox-test-project"):
                with mock.patch.object(
                    self.pi,
                    "ensure_project_row",
                    return_value={"id": 1, "project_key": "sandbox-test-project"},
                ):
                    with mock.patch.object(self.pi, "load_projects_config", return_value={}):
                        result = self.assign.assign_goal(
                            self.db,
                            "sandbox-test-project",
                            "Add a simple todo list",
                            objective="Ship todo list",
                            criteria=["tests pass"],
                            autonomous=True,
                        )
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result.get("autonomy"))
        self.assertTrue(result["autonomy"]["autonomous"])
        self.assertEqual(result["autonomy"]["acceptance_criteria"], ["tests pass"])


if __name__ == "__main__":
    unittest.main()
