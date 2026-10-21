#!/usr/bin/env python3
"""V0.9.1 auto-approval policy tests."""

from __future__ import annotations

import importlib.util
import sqlite3
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


class TestAutoApproval(unittest.TestCase):
    def setUp(self):
        self.policy = _load("anu-auto-approval-policy-v0.1.py", "anu_pol_t")
        self.state = _load("anu-autonomy-state-v0.1.py", "anu_state_pol")
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = Path(tmp.name)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY, project_key TEXT UNIQUE,
                name TEXT, repository TEXT, workspace TEXT, status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE goals (
                id INTEGER PRIMARY KEY, project_id INTEGER, description TEXT,
                status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER, goal_id INTEGER, action TEXT,
                requested_at TEXT, status TEXT, reason TEXT, risk TEXT,
                proposed_change TEXT, expires_at TEXT, classification TEXT,
                decided_by TEXT, decided_at TEXT
            );
            CREATE TABLE agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT, message TEXT, metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                task_id INTEGER, goal_id INTEGER
            );
            """
        )
        self.db.execute(
            """INSERT INTO projects VALUES (1,'sandbox','s','local','/tmp/s','active',datetime('now'))"""
        )
        self.db.execute(
            """INSERT INTO goals(id, project_id, description) VALUES (1,1,'todo')"""
        )
        self.db.commit()
        self.state.migrate(self.db)
        self.state.create_objective(self.db, 1, "todo", autonomous=True)

    def tearDown(self):
        self.db.close()
        self.path.unlink(missing_ok=True)

    def test_approve_research(self):
        r = self.policy.evaluate(self.db, capability="research", goal_id=1)
        self.assertEqual(r["decision"], "approve")

    def test_deny_deploy(self):
        r = self.policy.evaluate(self.db, capability="deploy", goal_id=1)
        self.assertEqual(r["decision"], "deny")

    def test_deny_force_push(self):
        r = self.policy.evaluate(
            self.db, capability="git_push", goal_id=1, force=True
        )
        self.assertEqual(r["decision"], "deny")

    def test_deny_default_branch_push(self):
        r = self.policy.evaluate(
            self.db, capability="git_push", goal_id=1, refspec="HEAD:main"
        )
        self.assertEqual(r["decision"], "deny")

    def test_approve_agent_branch_push(self):
        r = self.policy.evaluate(
            self.db, capability="git_push", goal_id=1, refspec="HEAD:agent/1-work"
        )
        self.assertEqual(r["decision"], "approve")

    def test_secret_scan(self):
        hits = self.policy.scan_secrets('api_key = "sk-abcdefghijklmnopqrstuvwxyz"')
        self.assertTrue(hits)

    def test_path_escape(self):
        bad = self.policy.path_escape(["../etc/passwd", "ok.py"])
        self.assertIn("../etc/passwd", bad)

    def test_protected_path(self):
        hits = self.policy.protected_path_hit([".github/workflows/ci.yml"])
        self.assertTrue(hits)

    def test_not_autonomous_defers(self):
        self.db.execute("UPDATE goals SET autonomous = 0 WHERE id = 1")
        self.db.commit()
        r = self.policy.evaluate(self.db, capability="research", goal_id=1)
        self.assertEqual(r["decision"], "defer")


if __name__ == "__main__":
    unittest.main()
