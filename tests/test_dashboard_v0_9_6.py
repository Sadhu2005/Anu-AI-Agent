#!/usr/bin/env python3
"""V0.9.6 dashboard auth + system metrics + control tests."""

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


class TestDashboardUnits(unittest.TestCase):
    def setUp(self):
        self.dash = _load("anu-dashboard-v0.1.py", "anu_dash_u")

    def test_token_compare(self):
        self.assertTrue(self.dash.token_ok("abc", "abc"))
        self.assertFalse(self.dash.token_ok("abc", "abd"))
        self.assertFalse(self.dash.token_ok("abc", None))

    def test_system_metrics_failsoft(self):
        metrics = None
        for name in ("system_metrics", "collect_system", "get_system"):
            if hasattr(self.dash, name):
                metrics = getattr(self.dash, name)()
                break
        if metrics is None and hasattr(self.dash, "api_system"):
            metrics = self.dash.api_system()
        # If helper exists, it must not raise on this OS.
        if metrics is not None:
            self.assertIsInstance(metrics, dict)


class TestDashboardHTTP(unittest.TestCase):
    def setUp(self):
        self.dash = _load("anu-dashboard-v0.1.py", "anu_dash_h")
        self.token = "test-token-v096"
        os.environ["ANU_DASHBOARD_TOKEN"] = self.token
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
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
                status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT, message TEXT, metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, goal_id INTEGER, task_id INTEGER
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
            "INSERT INTO projects VALUES (1,'sandbox','s','local','/tmp/s','active',datetime('now'))"
        )
        db.execute("INSERT INTO goals(id, project_id, description) VALUES (1,1,'todo')")
        db.commit()
        db.close()
        state = _load("anu-autonomy-state-v0.1.py", "anu_state_dash")
        db = state.db_connect(self.db_path)
        state.migrate(db)
        state.create_objective(db, 1, "todo", autonomous=True)
        db.close()

        self.dash.DB_FILE = self.db_path
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

    def test_auth_required(self):
        code, _ = self._req("/api/status", token="")
        self.assertEqual(code, 401)

    def test_status_ok(self):
        code, payload = self._req("/api/status")
        self.assertEqual(code, 200)
        self.assertIsInstance(payload, dict)

    def test_control_pause(self):
        code, payload = self._req(
            "/api/control",
            method="POST",
            data={"action": "pause", "goal_id": 1, "reason": "unit"},
        )
        self.assertIn(code, (200, 201), msg=str(payload))
        state = _load("anu-autonomy-state-v0.1.py", "anu_state_dash2")
        db = state.db_connect(self.db_path)
        ctl = state.effective_control(db, 1)
        db.close()
        self.assertEqual(ctl["state"], "pause")


if __name__ == "__main__":
    unittest.main()
