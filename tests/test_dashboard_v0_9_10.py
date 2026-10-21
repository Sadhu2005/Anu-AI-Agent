#!/usr/bin/env python3
"""V0.9.10 dashboard tabbed UI + API polish tests."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


def _load(name, mod_name):
    path = BIN / name
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDashboardHelpers(unittest.TestCase):
    def setUp(self):
        self.dash = _load("anu-dashboard-v0.1.py", "anu_dash_910_u")

    def test_format_approval_row_separator(self):
        row = self.dash.format_approval_row(
            {"id": 1, "action": "research", "reason": "look up API", "status": "pending"}
        )
        self.assertEqual(row["label"], "research — look up API")
        self.assertEqual(row["action"], "research")

    def test_index_html_has_tabs(self):
        html = self.dash.INDEX_HTML
        for tab in ("overview", "goal", "runtime", "activity", "approvals", "assign"):
            self.assertIn(f'data-tab="{tab}"', html)
            self.assertIn(f'data-panel="{tab}"', html)
        self.assertIn("/api/activity", html)
        self.assertIn("activity-stream", html)
        self.assertIn('id="system-panel"', html)
        self.assertIn("resp.goal || resp", html)
        self.assertIn('id="goal-filter"', html)
        self.assertIn("<select", html)


class TestDashboardHTTP910(unittest.TestCase):
    def setUp(self):
        self.dash = _load("anu-dashboard-v0.1.py", "anu_dash_910_h")
        self.token = "test-token-v0910"
        os.environ["ANU_DASHBOARD_TOKEN"] = self.token
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.ws = Path(tempfile.mkdtemp())
        proj_root = self.ws / "sandbox"
        proj_root.mkdir(parents=True)
        (proj_root / "AUTONOMY_PROGRESS.md").write_text(
            "# Progress\nline one\nline two\n", encoding="utf-8"
        )

        db = sqlite3.connect(str(self.db_path))
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
                id INTEGER PRIMARY KEY, goal_id INTEGER, title TEXT,
                status TEXT DEFAULT 'pending', priority INTEGER DEFAULT 50,
                attempts INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT, message TEXT, metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, goal_id INTEGER, task_id INTEGER
            );
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER, observation_type TEXT, content TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE llm_context (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER, task_id INTEGER, role TEXT, content TEXT,
                summary TEXT, provider TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, goal_id INTEGER,
                action TEXT, status TEXT DEFAULT 'pending', requested_at TEXT,
                reason TEXT, risk TEXT, proposed_change TEXT, expires_at TEXT,
                classification TEXT, decided_by TEXT, decided_at TEXT
            );
            """
        )
        db.execute(
            "INSERT INTO projects VALUES (1,'sandbox','s','local',?, 'active',datetime('now'))",
            (str(proj_root),),
        )
        db.execute("INSERT INTO goals(id, project_id, description) VALUES (1,1,'todo')")
        db.execute(
            "INSERT INTO tasks(id, goal_id, title, status) VALUES (9,1,'llm:research','pending')"
        )
        db.execute(
            """INSERT INTO agent_events(event_type, message, metadata, goal_id, task_id)
               VALUES ('PLAN_SAVED','plan v1','{}',1,9)"""
        )
        db.execute(
            """INSERT INTO agent_events(event_type, message, metadata, goal_id, task_id)
               VALUES ('TASK_STATE','running','{}',1,9)"""
        )
        db.execute(
            """INSERT INTO llm_context(goal_id, task_id, role, content)
               VALUES (1,9,'assistant','I will research greet helpers')"""
        )
        db.execute(
            """INSERT INTO approvals(task_id, goal_id, action, status, reason)
               VALUES (1,1,'capability_ops','pending','needs research')"""
        )
        db.commit()
        db.close()

        state = _load("anu-autonomy-state-v0.1.py", "anu_state_910")
        db = state.db_connect(self.db_path)
        state.migrate(db)
        state.create_objective(db, 1, "todo", autonomous=True)
        db.close()

        self.dash.DB_FILE = self.db_path
        self.dash.WORKSPACE = self.ws
        self.server = self.dash.ThreadingHTTPServer(
            ("127.0.0.1", 0), self.dash.make_handler(self.token)
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.db_path.unlink(missing_ok=True)

    def _req(self, path, *, method="GET", data=None, token=None, csrf=True):
        headers = {}
        tok = self.token if token is None else token
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
            if method == "POST" and csrf:
                headers["X-ANU-Token"] = tok
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"raw": raw}
                return resp.status, payload
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return exc.code, payload

    def test_root_serves_tabbed_spa(self):
        req = request.Request(f"http://127.0.0.1:{self.port}/")
        with request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8")
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        for name in ("Overview", "Active Goal", "Runtime", "Activity", "Approvals", "Assign"):
            self.assertIn(name, html)
        self.assertIn('id="system-panel"', html)

    def test_goals_list_slim_fields(self):
        code, payload = self._req("/api/goals")
        self.assertEqual(code, 200)
        goals = payload.get("goals") or []
        self.assertTrue(goals)
        g = goals[0]
        self.assertIn("id", g)
        self.assertIn("status", g)
        self.assertIn("description", g)

    def test_goal_detail_has_checklist_path(self):
        code, payload = self._req("/api/goals/1")
        self.assertEqual(code, 200)
        self.assertTrue(payload.get("ok"))
        self.assertIn("goal", payload)
        self.assertIsInstance(payload["goal"].get("checklist"), list)

    def test_goal_progress_excerpt(self):
        code, payload = self._req("/api/goals/1/progress")
        self.assertEqual(code, 200)
        self.assertTrue(payload.get("ok"), msg=str(payload))
        self.assertIn("Progress", payload.get("text") or "")

    def test_approvals_have_label(self):
        code, payload = self._req("/api/approvals")
        self.assertEqual(code, 200)
        approvals = payload.get("approvals") or []
        self.assertTrue(approvals)
        self.assertIn(" — ", approvals[0].get("label") or "")

    def test_activity_replay_feed(self):
        code, payload = self._req("/api/activity?goal=1")
        self.assertEqual(code, 200)
        self.assertTrue(payload.get("ok"), msg=str(payload))
        items = payload.get("items") or []
        self.assertTrue(items)
        phases = {it.get("phase") for it in items}
        self.assertTrue(phases & {"planning", "thinking"})
        kinds = {it.get("kind") for it in items}
        self.assertIn("PLAN_SAVED", kinds)
        self.assertTrue(any(str(it.get("kind", "")).startswith("llm:") for it in items))


if __name__ == "__main__":
    unittest.main()
