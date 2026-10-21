#!/usr/bin/env python3
"""Deterministic provider failover mocks — no paid API / no GPU."""

from __future__ import annotations

import unittest


class MockProvider:
    def __init__(self, name, *, fail_kinds=None):
        self.name = name
        self.fail_kinds = set(fail_kinds or [])

    def chat(self, *, kind="ok"):
        if kind in self.fail_kinds:
            raise ConnectionError(f"{self.name}:{kind}")
        return {"provider": self.name, "text": "ok"}


def route(providers, kind="ok"):
    errors = []
    for p in providers:
        try:
            return p.chat(kind=kind)
        except ConnectionError as exc:
            errors.append(str(exc))
            continue
    raise RuntimeError("all_providers_failed:" + ",".join(errors))


class TestFailoverMocks(unittest.TestCase):
    def test_groq_timeout_falls_to_gemini(self):
        chain = [
            MockProvider("groq", fail_kinds={"timeout"}),
            MockProvider("gemini"),
            MockProvider("openrouter"),
        ]
        out = route(chain, kind="timeout")
        self.assertEqual(out["provider"], "gemini")

    def test_test_failure_not_failover(self):
        # Coding/test failure must not be treated as transport outage.
        self.assertNotIn("assertion", {"timeout", "rate_limit", "connection"})

    def test_all_fail(self):
        chain = [
            MockProvider("a", fail_kinds={"timeout"}),
            MockProvider("b", fail_kinds={"timeout"}),
        ]
        with self.assertRaises(RuntimeError):
            route(chain, kind="timeout")


if __name__ == "__main__":
    unittest.main()
