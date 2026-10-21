#!/usr/bin/env python3
"""V0.9.13 test matrix + diagnose unit tests."""

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


class TestMatrix(unittest.TestCase):
    def setUp(self):
        self.matrix = _load("anu-test-matrix-v0.1.py", "anu_matrix_t")
        self.diag = _load("anu-failure-diagnose-v0.1.py", "anu_diag_t")
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        (self.root / "src").mkdir()
        (self.root / "tests" / "unit").mkdir(parents=True)
        (self.root / "tests" / "unit" / "test_ok.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "  def test_ok(self):\n    self.assertTrue(True)\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.td.cleanup()

    def test_detect_pytest_layout(self):
        d = self.matrix.detect_matrix(self.root)
        self.assertTrue(d["layers"]["unit"]["available"])
        self.assertFalse(d["layers"]["e2e"]["available"])

    def test_skip_missing_e2e(self):
        report = self.matrix.run_matrix(self.root)
        e2e = next(r for r in report["results"] if r["layer"] == "e2e")
        self.assertEqual(e2e["status"], "SKIPPED")
        self.assertTrue(report["ok"])  # unit pass; skips ok

    def test_unit_pass(self):
        report = self.matrix.run_matrix(self.root)
        unit = next(r for r in report["results"] if r["layer"] == "unit")
        self.assertEqual(unit["status"], "PASS")

    def test_failing_unit_blocks(self):
        (self.root / "tests" / "unit" / "test_bad.py").write_text(
            "import unittest\nclass B(unittest.TestCase):\n"
            "  def test_bad(self):\n    self.assertEqual(1, 2)\n",
            encoding="utf-8",
        )
        report = self.matrix.run_matrix(self.root)
        self.assertFalse(report["ok"])
        self.assertIn("unit", report["failed"])

    def test_classifier_assertion(self):
        c = self.diag.classify_failure("", "AssertionError: 1 != 2")
        self.assertEqual(c["class"], "assertion")

    def test_budget_exhausted(self):
        self.assertTrue(self.diag.budget_exhausted({"assertion": 3}, "assertion"))
        self.assertFalse(self.diag.budget_exhausted({"assertion": 1}, "assertion"))

    def test_diagnosis_write(self):
        report = {
            "results": [{
                "layer": "unit",
                "status": "FAIL",
                "reason": "exit_1",
                "stdout_tail": "FAIL: test_bad",
                "stderr_tail": 'File "x.py", line 3\nAssertionError',
            }]
        }
        d = self.diag.diagnose_matrix_report(report)
        path = self.diag.write_diagnosis(self.root, d)
        self.assertTrue(path)
        self.assertTrue((self.root / ".anu" / "DIAGNOSIS.md").is_file())


class TestPrGated(unittest.TestCase):
    def test_never_auto_merge(self):
        git = _load("anu-git-workflow-v0.1.py", "anu_git_pr")
        out = git.create_pr_gated(title="x", head_branch="agent/1-work")
        self.assertTrue(out.get("needs_approval"))
        self.assertFalse(out.get("auto_merge", True) is True and out.get("ok"))
        approved = git.create_pr_gated(
            approved=True, title="x", head_branch="agent/1-work",
            matrix_evidence={"results": [{"layer": "unit", "status": "PASS", "reason": "exit_0"}]},
        )
        self.assertFalse(approved.get("auto_merge"))
        self.assertIn("Test matrix", approved["payload"]["body"])


class TestWorkspaceLock(unittest.TestCase):
    def test_one_writer(self):
        lock = _load("anu-workspace-lock-v0.1.py", "anu_lock_t")
        with tempfile.TemporaryDirectory() as td:
            a = lock.acquire(td, owner="a")
            self.assertTrue(a["ok"])
            b = lock.acquire(td, owner="b")
            self.assertFalse(b["ok"])
            lock.release(td, owner="a")
            c = lock.acquire(td, owner="b")
            self.assertTrue(c["ok"])


if __name__ == "__main__":
    unittest.main()
