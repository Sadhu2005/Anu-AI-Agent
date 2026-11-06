#!/usr/bin/env python3
"""V1.3 anu-hermes skill set: blank-slate tools, air gap, local Ollama."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "docs" / "experiments" / "hermes-v1.3-apply-skills.py"


def _load():
    spec = importlib.util.spec_from_file_location("hermes_skillset_v13", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestHermesSkillsetV13(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_skillset_locks_tools_network_and_ollama(self):
        cfg = {
            "model": {
                "provider": "openrouter",
                "default": "openrouter/auto",
                "base_url": "https://openrouter.ai/api/v1",
            },
            "platform_toolsets": {"cli": ["web", "browser", "skills", "file"]},
            "agent": {"disabled_toolsets": []},
            "terminal": {
                "docker_network": True,
                "docker_forward_env": ["GITHUB_TOKEN"],
                "docker_volumes": ["/srv/anu/secrets:/secrets:ro"],
                "docker_extra_args": ["--network=host"],
            },
            "telegram": {"enabled": True},
        }
        out = self.mod.apply_skillset(cfg)
        self.assertEqual(out["platform_toolsets"]["cli"], ["file", "terminal"])
        disabled = out["agent"]["disabled_toolsets"]
        for name in ("skills", "delegation", "cronjob", "browser", "web", "computer_use"):
            self.assertIn(name, disabled)
        self.assertFalse(out["terminal"]["docker_network"])
        self.assertEqual(out["terminal"]["container_cpu"], 1)
        self.assertEqual(out["terminal"]["container_memory"], 2048)
        joined = " ".join(out["terminal"]["docker_volumes"])
        self.assertNotIn("/srv/anu", joined)
        self.assertNotIn("GITHUB_TOKEN", out["terminal"]["docker_forward_env"])
        self.assertEqual(out["model"]["provider"], "lmstudio")
        self.assertEqual(out["model"]["default"], "qwen2.5-coder:7b")
        self.assertIn("11434", out["model"]["base_url"])
        self.assertEqual(out["model"]["api_key"], "ollama")
        self.assertEqual(out["model"]["context_length"], 21299)
        self.assertEqual(out["model"]["ollama_num_ctx"], 21299)
        self.assertFalse(out["telegram"]["enabled"])
        self.assertFalse(out["memory"]["memory_enabled"])


if __name__ == "__main__":
    unittest.main()
