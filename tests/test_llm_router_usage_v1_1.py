#!/usr/bin/env python3
"""V1.1 LLM router usage / USD accounting tests."""

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


class TestLlmRouterUsageV11(unittest.TestCase):
    def setUp(self):
        self.router = _load("anu-llm-router-v0.1.py", "anu_router_usd_v11")
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.path = Path(tmp.name)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        # Minimal schema for provider_usage / health used by available checks
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS provider_usage (
                provider TEXT NOT NULL,
                day TEXT NOT NULL,
                requests INTEGER NOT NULL DEFAULT 0,
                tokens INTEGER NOT NULL DEFAULT 0,
                usd REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (provider, day)
            );
            CREATE TABLE IF NOT EXISTS provider_health (
                provider TEXT PRIMARY KEY,
                state TEXT,
                cooldown_until TEXT,
                last_error TEXT,
                error_class TEXT,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                updated_at TEXT
            );
            """
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_estimate_usd_zero_cost(self):
        usd = self.router.estimate_usd(
            {"name": "local", "zero_cost": True, "usd_per_1m_tokens": 99.0},
            1_000_000,
        )
        self.assertEqual(usd, 0.0)

    def test_estimate_usd_paid(self):
        # 2M tokens * $0.50 / 1M = $1.00
        usd = self.router.estimate_usd(
            {"name": "groq", "zero_cost": False, "usd_per_1m_tokens": 0.50},
            2_000_000,
        )
        self.assertAlmostEqual(usd, 1.0, places=6)

    def test_bump_usage_records_non_zero_usd(self):
        provider = {
            "name": "paid_fixture",
            "enabled": True,
            "zero_cost": False,
            "usd_per_1m_tokens": 1.0,
            "daily_requests": 100,
            "daily_tokens": 10_000_000,
            "max_usd_per_day": 5.0,
        }
        tokens = 500_000  # → $0.50
        self.router.bump_usage(
            self.db,
            provider["name"],
            tokens=tokens,
            usd=self.router.estimate_usd(provider, tokens),
        )
        usage = self.router.get_usage(self.db, provider["name"])
        self.assertEqual(int(usage["requests"]), 1)
        self.assertEqual(int(usage["tokens"]), tokens)
        self.assertAlmostEqual(float(usage["usd"]), 0.5, places=6)

    def test_max_usd_per_day_trips_provider_available(self):
        provider = {
            "name": "capped",
            "enabled": True,
            "zero_cost": False,
            "usd_per_1m_tokens": 1.0,
            "daily_requests": 1000,
            "daily_tokens": 10_000_000,
            "max_usd_per_day": 0.25,
        }
        # First bump under the cap
        self.router.bump_usage(
            self.db, "capped", tokens=100_000, usd=0.10,
        )
        ok, reason = self.router.provider_available(self.db, provider)
        self.assertTrue(ok, msg=reason)

        # Cross the daily USD cap
        self.router.bump_usage(
            self.db, "capped", tokens=200_000, usd=0.20,
        )
        ok2, reason2 = self.router.provider_available(self.db, provider)
        self.assertFalse(ok2)
        self.assertEqual(reason2, "daily_usd")


if __name__ == "__main__":
    unittest.main()
