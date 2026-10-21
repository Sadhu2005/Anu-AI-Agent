#!/usr/bin/env python3
"""V0.9.3 change generator + repo inspector tests."""

from __future__ import annotations

import importlib.util
import json
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


class TestChangeGenerator(unittest.TestCase):
    def setUp(self):
        self.gen = _load("anu-change-generator-v0.1.py", "anu_gen_t")
        self.insp = _load("anu-repo-inspector-v0.1.py", "anu_insp_t")

    def test_validate_ok(self):
        doc = {
            "files": [
                {"path": "todo.py", "action": "create", "content": "x=1\n"},
            ]
        }
        out = self.gen.validate_changes(doc)
        self.assertTrue(out["ok"])

    def test_reject_escape(self):
        with self.assertRaises(ValueError):
            self.gen.validate_changes({
                "files": [{"path": "../x.py", "action": "create", "content": "x"}]
            })

    def test_detect_unittest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "test_foo.py").write_text("import unittest\n", encoding="utf-8")
            cmd = self.insp.detect_test_command(root)
            self.assertEqual(cmd["kind"], "unittest")

    def test_todo_demo(self):
        with tempfile.TemporaryDirectory() as td:
            self.gen.WORKSPACE = Path(td)
            result = self.gen.generate_todo_list_changes("demo")
            self.assertTrue(result["ok"])
            self.assertTrue(Path(result["path"]).is_file())

    def test_todo_idempotent_after_apply(self):
        with tempfile.TemporaryDirectory() as td:
            self.gen.WORKSPACE = Path(td)
            first = self.gen.generate_todo_list_changes("demo")
            self.assertEqual(first["mode"], "apply")
            # Simulate successful apply: write desired content.
            root = Path(td) / "demo"
            desired_todo, desired_test = self.gen._todo_sources()
            (root / "todo.py").write_text(desired_todo, encoding="utf-8")
            (root / "test_todo.py").write_text(desired_test, encoding="utf-8")
            second = self.gen.generate_todo_list_changes("demo")
            self.assertEqual(second["mode"], "noop")
            doc = json.loads(Path(second["path"]).read_text(encoding="utf-8"))
            self.assertTrue(all(e["old"] == e["new"] for e in doc))

    def test_prepare_inspect_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            self.gen.WORKSPACE = Path(td)
            root = Path(td) / "demo"
            root.mkdir()
            (root / "app.py").write_text("print(1)\n", encoding="utf-8")
            out = self.gen.prepare_for_task("demo", "inspect", "anything")
            self.assertEqual(out["mode"], "noop")

    def test_placeholder_goal(self):
        self.assertTrue(self.gen.is_placeholder_goal("…"))
        self.assertTrue(self.gen.is_placeholder_goal("..."))
        self.assertFalse(self.gen.is_placeholder_goal("Build a todo list"))


if __name__ == "__main__":
    unittest.main()
