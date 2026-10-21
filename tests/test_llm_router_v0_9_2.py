#!/usr/bin/env python3
"""V0.9.2 LLM router tests (mocked HTTP)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
import urllib.error
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


class TestLlmRouter(unittest.TestCase):
    def setUp(self):
        self.router = _load("anu-llm-router-v0.1.py", "anu_router_t")
        self.state = _load("anu-autonomy-state-v0.1.py", "anu_state_r")
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = Path(tmp.name)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE goals (
                id INTEGER PRIMARY KEY, project_id INTEGER, description TEXT,
                status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT, message TEXT, metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, goal_id INTEGER
            );
            CREATE TABLE agent_control (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT, goal_id INTEGER, state TEXT, reason TEXT,
                by_who TEXT, updated_at TEXT, UNIQUE(scope, goal_id)
            );
            """
        )
        self.db.execute("INSERT INTO goals(id, description) VALUES (1, 'todo')")
        self.db.commit()
        self.state.migrate(self.db)
        self.state.create_objective(self.db, 1, "todo", autonomous=True)
        self.cfg = {
            "enabled": True,
            "defaults": {"context_chars": 8000},
            "privacy": {
                "forbidden_classes": ["SECRET"],
                "redact_patterns": [r"sk-[A-Za-z0-9]+"],
            },
            "cooldowns": {"rate_limit": 1, "connection": 1, "timeout": 1, "auth": 1, "quota": 1, "server_error": 1},
            "providers": [
                {
                    "name": "lmstudio",
                    "enabled": True,
                    "base_url": "http://127.0.0.1:9/v1",
                    "model": "local",
                    "local": True,
                    "zero_cost": True,
                    "daily_requests": 100,
                    "daily_tokens": 100000,
                },
                {
                    "name": "groq",
                    "enabled": True,
                    "base_url": "http://127.0.0.1:9/v1",
                    "model": "groq-model",
                    "local": False,
                    "zero_cost": False,
                    "daily_requests": 100,
                    "daily_tokens": 100000,
                    "max_usd_per_day": 1.0,
                },
            ],
        }

    def tearDown(self):
        self.db.close()
        self.path.unlink(missing_ok=True)

    def test_redaction(self):
        text = self.router.redact_text("token=sk-abcdefg hij", self.cfg["privacy"]["redact_patterns"])
        self.assertIn("[REDACTED]", text)

    def test_privacy_blocks_secret_remote(self):
        ok = self.router.privacy_allows("SECRET", self.cfg["providers"][1], self.cfg)
        self.assertFalse(ok)
        ok_local = self.router.privacy_allows("SECRET", self.cfg["providers"][0], self.cfg)
        self.assertTrue(ok_local)

    def test_failover_order(self):
        calls = []

        def fake_http(provider, messages, timeout=None):
            calls.append(provider["name"])
            if provider["name"] == "lmstudio":
                raise urllib.error.URLError("refused")
            return {
                "choices": [{"message": {"content": "hello from groq"}}],
                "usage": {"total_tokens": 3},
            }

        with mock.patch.object(self.router, "_http_chat", side_effect=fake_http):
            result = self.router.chat(
                self.db,
                goal_id=1,
                user_prompt="hi",
                config=self.cfg,
            )
        self.assertEqual(result["provider"], "groq")
        self.assertEqual(calls, ["lmstudio", "groq"])
        # Memory persisted under goal
        ctx = self.router.list_context(self.db, 1)
        roles = [c["role"] for c in ctx]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_memory_stable_across_switch(self):
        self.router.store_context(
            self.db, goal_id=1, task_id=None, role="user",
            content="remember the checklist", provider="lmstudio",
        )
        before = self.router.list_context(self.db, 1)

        def fake_http(provider, messages, timeout=None):
            if provider["name"] == "lmstudio":
                raise TimeoutError("timeout")
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 1},
            }

        with mock.patch.object(self.router, "_http_chat", side_effect=fake_http):
            self.router.chat(self.db, goal_id=1, user_prompt="continue", config=self.cfg)
        after = self.router.list_context(self.db, 1)
        self.assertTrue(any(c["content"] == "remember the checklist" for c in after))
        self.assertGreaterEqual(len(after), len(before))

    def test_daily_limit(self):
        self.router.bump_usage(self.db, "lmstudio", tokens=1)
        self.cfg["providers"][0]["daily_requests"] = 1
        ok, why = self.router.provider_available(self.db, self.cfg["providers"][0], self.cfg)
        self.assertFalse(ok)
        self.assertEqual(why, "daily_requests")

    def test_classify_429(self):
        err = urllib.error.HTTPError("http://x", 429, "no", hdrs=None, fp=None)
        self.assertEqual(self.router.classify_error(err), "rate_limit")


if __name__ == "__main__":
    unittest.main()
