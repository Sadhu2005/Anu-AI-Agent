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
            # Product tree must exist outside .anu/ before claiming progress.
            product_files = [
                root / "src" / "labapp" / "__init__.py",
                root / "tests" / "unit" / "test_ok.py",
            ]
            for pf in product_files:
                self.assertTrue(pf.is_file(), msg=f"missing product file {pf}")

            evidence = {
                "tasks": [],
                "human_next": False,
                "product_files": [str(p.relative_to(root)).replace("\\", "/") for p in product_files],
                "anu_only": False,
                "push": {"status": "skip", "reason": "unit_pack_no_network"},
            }
            for i, name in enumerate(
                ["scaffold", "integration", "repair", "docs", "regression"], start=1
            ):
                report = matrix.run_matrix(root)
                self.assertTrue(report["ok"], msg=str(report))
                matrix.write_matrix_artifact(root, report)
                # Distinguish real product artifacts from .anu-only churn.
                anu_art = root / ".anu" / "MATRIX_RESULTS.json"
                self.assertTrue(anu_art.is_file())
                self.assertTrue(
                    any(p.is_file() for p in product_files),
                    msg="progress must include product files, not only .anu/",
                )
                evidence["tasks"].append({
                    "id": i,
                    "name": name,
                    "matrix": report["summary"],
                    "product_ok": True,
                    "push_status": "skip",  # pack never silently claims push OK
                })
            path = root / ".anu" / "LAB_E2E_EVIDENCE.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(loaded["tasks"]), 5)
            self.assertFalse(loaded["human_next"])
            self.assertFalse(loaded["anu_only"])
            self.assertEqual(loaded["push"]["status"], "skip")
            self.assertTrue(all(t["push_status"] == "skip" for t in loaded["tasks"]))
            self.assertTrue(all(t.get("product_ok") for t in loaded["tasks"]))

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
