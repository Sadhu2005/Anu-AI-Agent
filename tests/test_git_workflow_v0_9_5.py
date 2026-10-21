#!/usr/bin/env python3
"""V0.9.5 git workflow guards."""

from __future__ import annotations

import importlib.util
import subprocess
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


class TestGitWorkflow(unittest.TestCase):
    def setUp(self):
        self.git = _load("anu-git-workflow-v0.1.py", "anu_git_t")

    def test_refspec_agent_ok(self):
        dest = self.git.validate_push_refspec("HEAD:agent/1-work", default="main")
        self.assertEqual(dest, "agent/1-work")

    def test_refspec_default_denied(self):
        with self.assertRaises(PermissionError):
            self.git.validate_push_refspec("HEAD:main", default="main")

    def test_force_denied(self):
        with self.assertRaises(PermissionError):
            self.git.validate_push_refspec("--force agent/1-x", default="main")

    def test_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "t@t"],
                cwd=root, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "t"],
                cwd=root, check=True, capture_output=True,
            )
            (root / "a.py").write_text("x=1\n", encoding="utf-8")
            subprocess.run(["git", "add", "a.py"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=root, check=True, capture_output=True,
            )
            (root / "user.txt").write_text("mine\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                self.git.require_clean_or_abort(root)
            # progress file alone is OK
            (root / "user.txt").unlink()
            (root / "AUTONOMY_PROGRESS.md").write_text("# x\n", encoding="utf-8")
            self.assertTrue(self.git.require_clean_or_abort(root))

    def test_ensure_branch_from_detached(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "t@t"],
                cwd=root, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "t"],
                cwd=root, check=True, capture_output=True,
            )
            (root / "a.py").write_text("x=1\n", encoding="utf-8")
            subprocess.run(["git", "add", "a.py"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=root, check=True, capture_output=True,
            )
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            subprocess.run(["git", "checkout", "--detach", head], cwd=root, check=True, capture_output=True)
            result = self.git.ensure_agent_branch(root, 42, "todo")
            self.assertTrue(result["ok"])
            self.assertTrue(result["branch"].startswith("agent/42-"))


if __name__ == "__main__":
    unittest.main()
