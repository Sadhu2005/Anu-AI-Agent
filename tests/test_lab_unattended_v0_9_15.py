#!/usr/bin/env python3
"""V0.9.15 unattended lab fixture — simulated 5-task cadence without human next."""

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


def _seed_lab(root: Path):
    (root / "src" / "labapp").mkdir(parents=True)
    (root / "src" / "labapp" / "__init__.py").write_text(
        'def greet(n="world"):\n    return f"hello, {n}"\n', encoding="utf-8"
    )
    for layer in ("unit", "integration", "functional", "e2e", "regression"):
        d = root / "tests" / layer
        d.mkdir(parents=True)
        (d / "test_ok.py").write_text(
            "import unittest,sys\nfrom pathlib import Path\n"
            "sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))\n"
            "from labapp import greet\n"
            "class T(unittest.TestCase):\n"
            "  def test_ok(self):\n    self.assertIn('hello', greet())\n",
            encoding="utf-8",
        )


class TestLabUnattended(unittest.TestCase):
    def test_five_slices_matrix_green(self):
        matrix = _load("anu-test-matrix-v0.1.py", "anu_matrix_lab")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_lab(root)
            evidence = {"tasks": [], "human_next": False}
            for i, name in enumerate(
                ["scaffold", "integration", "repair", "docs", "regression"], start=1
            ):
                report = matrix.run_matrix(root)
                self.assertTrue(report["ok"], msg=str(report))
                matrix.write_matrix_artifact(root, report)
                evidence["tasks"].append({
                    "id": i,
                    "name": name,
                    "matrix": report["summary"],
                    "pushed": False,  # unit pack does not network-push
                })
            path = root / ".anu" / "LAB_E2E_EVIDENCE.json"
            path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            self.assertEqual(len(evidence["tasks"]), 5)
            self.assertFalse(evidence["human_next"])

    def test_injected_fail_then_diagnose(self):
        matrix = _load("anu-test-matrix-v0.1.py", "anu_matrix_lab2")
        diag = _load("anu-failure-diagnose-v0.1.py", "anu_diag_lab2")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_lab(root)
            bad = root / "tests" / "unit" / "test_bad.py"
            bad.write_text(
                "import unittest\nclass B(unittest.TestCase):\n"
                "  def test_boom(self):\n    self.assertEqual(1,0)\n",
                encoding="utf-8",
            )
            fail = matrix.run_matrix(root)
            self.assertFalse(fail["ok"])
            d = diag.diagnose_matrix_report(fail)
            diag.write_diagnosis(root, d)
            self.assertEqual(d["primary_class"], "assertion")
            bad.unlink()
            ok = matrix.run_matrix(root)
            self.assertTrue(ok["ok"])


if __name__ == "__main__":
    unittest.main()
