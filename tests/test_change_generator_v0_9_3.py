#!/usr/bin/env python3
"""V0.9.3 change generator + repo inspector tests."""

from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
