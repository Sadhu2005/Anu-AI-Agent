#!/usr/bin/env python3
"""V1.4 unit: job request, path rules, and worker env. No process spawn."""

from __future__ import annotations

import importlib.util
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


class TestHermesAdapterUnitV14(unittest.TestCase):
    def setUp(self):
        self.adapter = _load("anu-hermes-adapter-v0.1.py", "hermes_adapter_unit_v14")
        self.worker = _load("anu-hermes-worker-v0.1.py", "hermes_worker_unit_v14")
        self.result = _load("anu-hermes-result-v0.1.py", "hermes_result_unit_v14")

    def _request(self, **overrides):
        fields = {
            "job_id": "anu-goal-001-task-003",
            "project": "lab-ping-module",
            "base_commit": "1a8cd22",
            "goal": "Add one clamp module and its unit test",
            "acceptance_criteria": ["clamp stays inside the range"],
            "role": "implement",
        }
        fields.update(overrides)
        return self.adapter.build_job_request(**fields)

    def test_request_rejects_push_and_production(self):
        request = self._request()
        self.assertFalse(request["git_push"])
        self.assertFalse(request["production_access"])
        self.assertFalse(request["network_access"])
        with self.assertRaises(ValueError):
            self._request(git_push=True)
        with self.assertRaises(ValueError):
            self._request(production_access=True)
        print("PASS unit request rejects push and production")

    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            with self.assertRaises(ValueError):
                self.result.assert_relative_path(workspace, "../etc/passwd")
            with self.assertRaises(ValueError):
                self.result.assert_relative_path(workspace, "/etc/passwd")
            with self.assertRaises(ValueError):
                self.result.normalize_result(
                    {"status": "completed", "changed_files": ["../secret"]},
                    workspace,
                )
        print("PASS unit path traversal rejected")

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "job"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()
            (outside / "secret").write_text("nope\n", encoding="utf-8")
            link = workspace / "leak"
            created = False
            try:
                link.symlink_to(outside / "secret", target_is_directory=False)
                created = True
            except OSError:
                created = False
            if created:
                with self.assertRaises(ValueError):
                    self.result.assert_relative_path(workspace, "leak")
                print("PASS unit symlink escape rejected")
                return
            real_is_symlink = Path.is_symlink
            real_resolve = Path.resolve

            def is_symlink(path):
                if Path(path).name == "leak":
                    return True
                return real_is_symlink(path)

            def resolve(path, *args, **kwargs):
                if Path(path).name == "leak":
                    return (outside / "secret").resolve()
                return real_resolve(path, *args, **kwargs)

            Path.is_symlink = is_symlink
            Path.resolve = resolve
            try:
                with self.assertRaises(ValueError):
                    self.result.assert_relative_path(workspace, "leak")
            finally:
                Path.resolve = real_resolve
                Path.is_symlink = real_is_symlink
        print("PASS unit symlink escape rejected")

    def test_worker_env_drops_credentials(self):
        env = self.worker.worker_env({
            "GITHUB_TOKEN": "ghp-example",
            "ANU_MODEL_API_KEY": "sk-example",
            "PATH": "/usr/bin",
            "HERMES_DOCKER_BINARY": "docker",
        })
        self.assertEqual(env, {"HERMES_DOCKER_BINARY": "podman"})
        self.assertNotIn("ghp-example", env.values())
        self.assertNotIn("sk-example", env.values())
        print("PASS unit worker env drops credentials")

    def test_unit_file_has_isolated_user_and_no_secrets(self):
        text = (ROOT / "deploy" / "systemd" / "anu-hermes.service").read_text(encoding="utf-8")
        self.assertIn("User=anu-hermes", text)
        self.assertNotIn("/srv/anu/secrets", text)
        self.assertNotIn("LoadCredential", text)
        self.assertNotIn("Environment=", text)
        config = (ROOT / "config" / "anu-hermes.yaml").read_text(encoding="utf-8")
        self.assertIn("enabled: false", config)
        self.assertIn("execute: false", config)
        print("PASS unit config and unit file stay closed")


if __name__ == "__main__":
    unittest.main()
