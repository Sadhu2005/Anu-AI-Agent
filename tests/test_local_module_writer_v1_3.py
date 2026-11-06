#!/usr/bin/env python3
"""Local module writer: exact paths, keep files only when tests pass."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
MODULE_REL = "src/labapp/clamp.py"
TEST_REL = "tests/unit/test_clamp.py"

MODULE_OK = (
    "def clamp(value: int, low: int, high: int) -> int:\n"
    "    return max(min(value, high), low)\n"
)
TEST_OK = (
    "import unittest\n"
    "from labapp.clamp import clamp\n"
    "\n"
    "class TestClamp(unittest.TestCase):\n"
    "    def test_inside(self):\n"
    "        self.assertEqual(clamp(3, 0, 5), 3)\n"
)
TEST_FAIL = (
    "import unittest\n"
    "from labapp.clamp import clamp\n"
    "\n"
    "class TestClamp(unittest.TestCase):\n"
    "    def test_inside(self):\n"
    "        self.assertEqual(clamp(3, 0, 5), 4)\n"
)


def _load():
    path = BIN / "anu-local-module-v0.1.py"
    spec = importlib.util.spec_from_file_location("anu_local_module_v13", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _reply(files):
    return json.dumps({"files": files})


def _pair(module=MODULE_OK, test=TEST_OK, module_rel=MODULE_REL, test_rel=TEST_REL):
    return _reply([
        {"file": module_rel, "content": module},
        {"file": test_rel, "content": test},
    ])


def _chat(replies):
    calls = []

    def chat(prompt, system, timeout):
        calls.append({"prompt": prompt, "system": system, "timeout": timeout})
        text = replies[min(len(calls) - 1, len(replies) - 1)]
        return {"ok": True, "text": text, "model": "fake", "zero_cost": True}

    return chat, calls


class TestLocalModuleWriter(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_goal_cycle_does_not_import_writer(self):
        text = (BIN / "anu-goal-cycle-v0.1.py").read_text(encoding="utf-8")
        self.assertNotIn("anu-local-module", text)

    def test_test_file_need_not_define_clamp(self):
        reason = self.mod.check_source(
            TEST_REL,
            TEST_OK,
            module_rel=MODULE_REL,
            test_rel=TEST_REL,
        )
        self.assertIsNone(reason)
        self.assertNotIn("def clamp", TEST_OK)

    def test_suffix_name_is_kept_when_tests_pass(self):
        chat, calls = _chat([_pair()])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "labapp").mkdir(parents=True)
            (root / "src" / "labapp" / "ping.py").write_text(
                "def ping():\n    return 'pong'\n", encoding="utf-8"
            )
            result = self.mod.propose_module(
                root,
                MODULE_REL,
                TEST_REL,
                "Add clamp.",
                chat=chat,
            )
            self.assertTrue(result["kept"])
            self.assertEqual(result["attempts"], 1)
            self.assertTrue(result["prompt_inside_budget"])
            written = (root / TEST_REL).read_text(encoding="utf-8")
            self.assertNotIn("def clamp", written)
            self.assertIn("def clamp", (root / MODULE_REL).read_text(encoding="utf-8"))
            self.assertIn("pong", (root / "src" / "labapp" / "ping.py").read_text(encoding="utf-8"))
            self.assertEqual(len(calls), 1)

    def test_extra_path_writes_nothing(self):
        bad = _reply([
            {"file": "src/labapp/other.py", "content": MODULE_OK},
            {"file": TEST_REL, "content": TEST_OK},
        ])
        chat, calls = _chat([bad, bad])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / "keep.txt"
            marker.write_text("stay\n", encoding="utf-8")
            result = self.mod.propose_module(
                root, MODULE_REL, TEST_REL, "Add clamp.", chat=chat
            )
            self.assertFalse(result["kept"])
            self.assertIn("path:", result["reason"])
            self.assertEqual(result["attempts"], 2)
            self.assertEqual(len(calls), 2)
            self.assertFalse((root / MODULE_REL).exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "stay\n")

    def test_banned_call_writes_nothing(self):
        banned = "def clamp(value, low, high):\n    return eval('1')\n"
        chat, calls = _chat([_pair(module=banned), _pair(module=banned)])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = self.mod.propose_module(
                root, MODULE_REL, TEST_REL, "Add clamp.", chat=chat
            )
            self.assertFalse(result["kept"])
            self.assertIn("banned_call", result["reason"])
            self.assertEqual(len(calls), 2)
            self.assertFalse((root / MODULE_REL).exists())

    def test_syntax_error_writes_nothing(self):
        broken = "def clamp(\n"
        chat, _calls = _chat([_pair(module=broken), _pair(module=broken)])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = self.mod.propose_module(
                root, MODULE_REL, TEST_REL, "Add clamp.", chat=chat
            )
            self.assertFalse(result["kept"])
            self.assertIn("syntax:", result["reason"])
            self.assertFalse((root / MODULE_REL).exists())

    def test_retry_then_keep(self):
        chat, calls = _chat([_pair(module="def clamp(\n"), _pair()])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = self.mod.propose_module(
                root, MODULE_REL, TEST_REL, "Add clamp.", chat=chat
            )
            self.assertTrue(result["kept"])
            self.assertEqual(result["attempts"], 2)
            self.assertEqual(len(calls), 2)
            self.assertTrue((root / MODULE_REL).is_file())

    def test_oracle_keeps_files(self):
        chat, _calls = _chat([_pair()])

        def oracle(root):
            src = str(Path(root) / "src")
            import sys
            sys.path.insert(0, src)
            for name in list(sys.modules):
                if name == "labapp" or name.startswith("labapp."):
                    del sys.modules[name]
            from labapp.clamp import clamp
            return clamp(10, 0, 5) == 5, "ok"

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = self.mod.propose_module(
                root, MODULE_REL, TEST_REL, "Add clamp.", chat=chat, oracle=oracle
            )
            self.assertTrue(result["kept"])
            self.assertTrue(result["oracle_ok"])
            self.assertTrue((root / MODULE_REL).is_file())
            self.assertTrue((root / TEST_REL).is_file())

    def test_failing_test_deletes_files_without_retry(self):
        chat, calls = _chat([_pair(test=TEST_FAIL), _pair()])
        seen = []

        def oracle(_root):
            seen.append(True)
            return True, "ok"

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "labapp").mkdir(parents=True)
            ping = root / "src" / "labapp" / "ping.py"
            ping.write_text("def ping():\n    return 'pong'\n", encoding="utf-8")
            result = self.mod.propose_module(
                root,
                MODULE_REL,
                TEST_REL,
                "Add clamp.",
                chat=chat,
                oracle=oracle,
            )
            self.assertFalse(result["kept"])
            self.assertEqual(result["reason"], "test_failed")
            self.assertEqual(len(calls), 1)
            self.assertEqual(seen, [])
            self.assertFalse((root / MODULE_REL).exists())
            self.assertFalse((root / TEST_REL).exists())
            self.assertEqual(ping.read_text(encoding="utf-8"), "def ping():\n    return 'pong'\n")

    def test_over_long_file_rejected(self):
        body = "def clamp(value, low, high):\n" + ("    return value\n" * 80)
        reason = self.mod.check_source(
            MODULE_REL, body, module_rel=MODULE_REL, test_rel=TEST_REL
        )
        self.assertIsNotNone(reason)
        self.assertTrue(reason.startswith("too_long:"))


if __name__ == "__main__":
    unittest.main()
