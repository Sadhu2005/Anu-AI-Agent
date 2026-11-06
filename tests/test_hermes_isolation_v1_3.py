#!/usr/bin/env python3
"""V1.3 Hermes isolation policy tests (no Podman required)."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "docs" / "experiments" / "hermes-v1.3-isolation.py"


def _load():
    spec = importlib.util.spec_from_file_location("hermes_v1_3_isolation", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestHermesIsolationV13(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_terminal_policy_strips_network_tokens_and_production_mounts(self):
        cfg = {
            "model": {"provider": "openrouter"},
            "terminal": {
                "backend": "local",
                "docker_network": True,
                "docker_forward_env": ["GITHUB_TOKEN", "LANG"],
                "docker_env": {"OPENROUTER_API_KEY": "sk-or-secret", "DEBUG": "1"},
                "docker_volumes": [
                    "/srv/anu/secrets:/secrets:ro",
                    "/var/run/docker.sock:/var/run/docker.sock",
                    "/var/lib/anu-hermes/jobs/keep:/data:ro",
                ],
                "docker_extra_args": ["--network=host", "--privileged", "--hostname=anu"],
            },
        }
        out = self.mod.apply_terminal_isolation(cfg, job_dir="/var/lib/anu-hermes/jobs/job1")
        term = out["terminal"]
        self.assertFalse(term["docker_network"])
        self.assertEqual(term["backend"], "docker")
        self.assertEqual(term["container_cpu"], 1)
        self.assertEqual(term["container_memory"], 2048)
        self.assertFalse(term["container_persistent"])
        self.assertFalse(term["docker_persist_across_processes"])
        self.assertEqual(term["docker_forward_env"], ["LANG"])
        self.assertNotIn("OPENROUTER_API_KEY", term["docker_env"])
        self.assertEqual(term["docker_env"].get("DEBUG"), "1")
        joined = " ".join(term["docker_volumes"])
        self.assertNotIn("/srv/anu", joined)
        self.assertNotIn("docker.sock", joined)
        self.assertIn("/var/lib/anu-hermes/jobs/job1:/workspace:rw", term["docker_volumes"])
        self.assertIn("/var/lib/anu-hermes/jobs/keep:/data:ro", term["docker_volumes"])
        self.assertEqual(term["docker_extra_args"], ["--hostname=anu"])
        self.assertEqual(out["model"]["provider"], "openrouter")

    def test_jobs_root_rejects_production_and_relative_paths(self):
        with self.assertRaises(self.mod.IsolationError):
            self.mod.assert_job_root_allowed("/srv/anu/jobs")
        with self.assertRaises(self.mod.IsolationError):
            self.mod.assert_job_root_allowed("relative/jobs")
        with self.assertRaises(self.mod.IsolationError):
            self.mod.assert_source_allowed("/srv/anu/secrets/github-token")
        with self.assertRaises(self.mod.IsolationError):
            self.mod.assert_source_allowed("/srv/anu/brain")

    def test_prepare_snapshot_skips_secrets_and_dispose_requires_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "src"
            (source / "secrets").mkdir(parents=True)
            (source / "secrets" / "github-token").write_text("nope", encoding="utf-8")
            (source / ".env").write_text("OPENROUTER_API_KEY=sk\n", encoding="utf-8")
            (source / "app.py").write_text("print(1)\n", encoding="utf-8")
            jobs = tmp_path / "jobs"
            original = self.mod.assert_job_root_allowed
            if not str(jobs).startswith("/"):
                def _allow_temp(path):
                    text = str(path).replace("\\", "/")
                    if "/srv/anu" in text:
                        raise self.mod.IsolationError(text)
                    if "docker.sock" in text:
                        raise self.mod.IsolationError(text)

                self.mod.assert_job_root_allowed = _allow_temp
            try:
                root = jobs
                root.mkdir()
                dest = self.mod.prepare_job_workspace(root, "job-a", source)
                self.assertTrue((dest / "app.py").is_file())
                self.assertFalse((dest / ".env").exists())
                self.assertFalse((dest / "secrets").exists())
                self.assertTrue((dest / self.mod.JOB_MARKER).is_file())
                self.mod.dispose_job_workspace(root, "job-a")
                self.assertFalse(dest.exists())
            finally:
                self.mod.assert_job_root_allowed = original

    def test_prepare_refuses_production_jobs_root(self):
        with self.assertRaises(self.mod.IsolationError):
            self.mod.prepare_job_workspace("/srv/anu/tmp-jobs", "job-a", None)

    def test_podman_probe_has_no_network_and_no_production_mount(self):
        args = self.mod.podman_probe_args("/var/lib/anu-hermes/jobs/job1")
        self.assertIn("--network=none", args)
        self.assertIn("--cap-drop=ALL", args)
        joined = " ".join(args)
        self.assertNotIn("/srv/anu", joined)
        self.assertNotIn("docker.sock", joined)
        with self.assertRaises(self.mod.IsolationError):
            self.mod.podman_probe_args("/srv/anu/workspace")

    def test_apply_writes_podman_binary_without_clobbering_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = home / ".env"
            env.write_text("OPENROUTER_API_KEY=sk-or-keep\nHERMES_DOCKER_BINARY=\n", encoding="utf-8")
            (home / "config.yaml").write_text("model:\n  provider: openrouter\n", encoding="utf-8")
            self.mod.apply_hermes_home(home, job_dir="/var/lib/anu-hermes/jobs/job9")
            text = env.read_text(encoding="utf-8")
            self.assertIn("OPENROUTER_API_KEY=sk-or-keep", text)
            self.assertIn("HERMES_DOCKER_BINARY=podman", text)
            written = (home / "config.yaml").read_text(encoding="utf-8")
            self.assertIn("docker_network: false", written)
            self.assertIn("/var/lib/anu-hermes/jobs/job9:/workspace:rw", written)


if __name__ == "__main__":
    unittest.main()
