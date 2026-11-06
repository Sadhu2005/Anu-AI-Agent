#!/usr/bin/env python3
"""V1.4 functional: one job request returns a checked result and does not spawn Hermes."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


def _load(filename: str, name: str):
    path = BIN / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _allow_temp_jobs(worker):
    iso = worker.isolation()
    original = iso.assert_job_root_allowed

    def _allow(path):
        text = str(path).replace("\\", "/")
        if "/srv/anu" in text:
            raise iso.IsolationError(text)

    if os.name == "nt" or not str(Path(tempfile.gettempdir())).startswith("/"):
        iso.assert_job_root_allowed = _allow
    return iso, original


class TestHermesJobFunctionalV14(unittest.TestCase):
    def setUp(self):
        self.adapter = _load("anu-hermes-adapter-v0.1.py", "hermes_adapter_fn_v14")
        self.worker = _load("anu-hermes-worker-v0.1.py", "hermes_worker_fn_v14")
        self.iso, self._original_allow = _allow_temp_jobs(self.worker)

    def tearDown(self):
        self.iso.assert_job_root_allowed = self._original_allow

    def test_completed_job_returns_unapplied_files(self):
        request = self.adapter.build_job_request(
            job_id="anu-goal-001-task-003",
            project="lab-ping-module",
            base_commit="1a8cd22",
            goal="Add one clamp module and its unit test",
            acceptance_criteria=["clamp stays inside the range"],
        )

        def runner(workspace, _env):
            target = workspace / "src" / "labapp"
            target.mkdir(parents=True)
            target.joinpath("clamp.py").write_text(
                "def clamp(value, low, high):\n    return max(low, min(value, high))\n",
                encoding="utf-8",
            )
            return {
                "job_id": request["job_id"],
                "status": "completed",
                "changed_files": ["src/labapp/clamp.py"],
                "files": [{
                    "file": "src/labapp/clamp.py",
                    "old": "",
                    "new": target.joinpath("clamp.py").read_text(encoding="utf-8"),
                }],
                "base_commit": request["base_commit"],
                "logs": "fixture",
                "commit": True,
                "push": True,
                "test_summary": {"unit": "PASS"},
            }

        with tempfile.TemporaryDirectory() as tmp:
            out = self.worker.run_job(
                request,
                jobs_root=Path(tmp) / "jobs",
                runner=runner,
            )
            workspace = Path(out["workspace"])
            self.assertTrue((workspace / "src" / "labapp" / "clamp.py").is_file())
            self.assertFalse(str(workspace).replace("\\", "/").startswith("/srv/anu"))
        self.assertTrue(out["ok"])
        self.assertEqual(out["changed_files"], ["src/labapp/clamp.py"])
        self.assertFalse(out["commit"])
        self.assertFalse(out["push"])
        self.assertEqual(out["base_commit"], "1a8cd22")
        self.assertEqual(out["test_summary"]["unit"], "PASS")
        print("PASS functional completed job stays unapplied")

    def test_disabled_execution_does_not_spawn(self):
        request = self.adapter.build_job_request(
            job_id="anu-goal-001-task-004",
            project="lab-ping-module",
            base_commit="1a8cd22",
            goal="Add clamp",
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = self.worker.run_job(request, jobs_root=Path(tmp) / "jobs")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "live_execution_disabled")
        self.assertEqual(out["files"], [])
        self.assertFalse(out["commit"])
        self.assertFalse(out["push"])
        print("PASS functional live execution stays disabled")


if __name__ == "__main__":
    unittest.main()
