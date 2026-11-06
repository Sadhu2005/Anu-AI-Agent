#!/usr/bin/env python3
"""V1.4 integration: adapter request, isolation workspace, result check, brain sentinel."""

from __future__ import annotations

import importlib.util
import json
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


class TestHermesJobIntegrationV14(unittest.TestCase):
    def setUp(self):
        self.adapter = _load("anu-hermes-adapter-v0.1.py", "hermes_adapter_int_v14")
        self.worker = _load("anu-hermes-worker-v0.1.py", "hermes_worker_int_v14")
        self.iso, self._original_allow = _allow_temp_jobs(self.worker)

    def tearDown(self):
        self.iso.assert_job_root_allowed = self._original_allow

    def test_worker_prepares_isolated_workspace(self):
        request = self.adapter.build_job_request(
            job_id="anu-goal-001-task-003",
            project="lab-ping-module",
            base_commit="1a8cd22",
            goal="Add clamp",
        )

        def runner(workspace, env):
            self.assertEqual(env.get("HERMES_DOCKER_BINARY"), "podman")
            self.assertNotIn("GITHUB_TOKEN", env)
            marker = workspace / self.iso.JOB_MARKER
            self.assertTrue(marker.is_file())
            body = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(body["job_id"], request["job_id"])
            self.assertNotIn("/srv/anu", body["workspace"].replace("\\", "/"))
            return {
                "job_id": request["job_id"],
                "status": "completed",
                "changed_files": ["src/clamp.py"],
                "files": [{"file": "src/clamp.py", "old": "", "new": "def clamp():\n    return 1\n"}],
                "base_commit": "1a8cd22",
                "commit": True,
                "push": True,
            }

        with tempfile.TemporaryDirectory() as tmp:
            out = self.worker.run_job(
                request,
                jobs_root=Path(tmp) / "jobs",
                runner=runner,
                parent_env={"GITHUB_TOKEN": "ghp-example", "PATH": "/usr/bin"},
            )
        self.assertEqual(out["status"], "completed")
        self.assertFalse(out["commit"])
        self.assertFalse(out["push"])
        self.assertEqual(out["files"][0]["new"].splitlines()[0], "def clamp():")
        print("PASS integration workspace, env, and result boundary")

    def test_killed_runner_leaves_brain_unchanged(self):
        request = self.adapter.build_job_request(
            job_id="anu-goal-001-task-009",
            project="lab-ping-module",
            base_commit="1a8cd22",
            goal="Add clamp",
        )

        def runner(_workspace, _env):
            return {"status": "killed", "commit": True, "push": True}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain.txt"
            brain.write_text("sentinel\n", encoding="utf-8")
            out = self.worker.run_job(
                request,
                jobs_root=root / "jobs",
                runner=runner,
                brain_path=brain,
            )
            self.assertEqual(out["status"], "failed")
            self.assertEqual(out["reason"], "killed")
            self.assertEqual(brain.read_text(encoding="utf-8"), "sentinel\n")
        print("PASS integration killed job leaves brain unchanged")

    def test_goal_cycle_does_not_import_adapter(self):
        text = (BIN / "anu-goal-cycle-v0.1.py").read_text(encoding="utf-8")
        self.assertNotIn("anu-hermes-adapter", text)
        self.assertNotIn("anu-hermes-worker", text)
        print("PASS integration goal cycle stays unwired")


if __name__ == "__main__":
    unittest.main()
