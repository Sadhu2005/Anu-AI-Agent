#!/usr/bin/env python3
"""Local Ollama provider contract: Qwen on the lab server, cloud failover off."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "bin" / "anu-llm-router-v0.1.py"
PROVIDERS = ROOT / "config" / "anu-llm-providers.yaml"
GATEWAY = ROOT / "config" / "anu-model-gateway.yaml"


def _load_router():
    spec = importlib.util.spec_from_file_location("anu_llm_router_ollama_v12", ROUTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestOllamaProviderV12(unittest.TestCase):
    def setUp(self):
        self.router = _load_router()
        self.cfg = self.router.load_config(PROVIDERS)

    def test_ollama_is_the_only_enabled_provider(self):
        providers = self.cfg["providers"]
        enabled = [p for p in providers if p.get("enabled")]
        self.assertEqual([p["name"] for p in enabled], ["ollama"])
        ollama = enabled[0]
        self.assertEqual(ollama["base_url"], "http://192.168.1.13:11434/v1")
        self.assertEqual(ollama["model"], "qwen2.5-coder:7b")
        self.assertTrue(ollama["local"])
        self.assertTrue(ollama["zero_cost"])
        self.assertEqual(ollama["api_key_env"], "ANU_OLLAMA_API_KEY")
        self.assertEqual(self.router.estimate_usd(ollama, 10_000), 0.0)
        self.assertEqual(ollama["window_tokens"], 32768)
        self.assertEqual(ollama["context_use_ratio"], 0.65)
        budget = self.router.context_char_budget(ollama, self.cfg["defaults"])
        # 65% of 32768 is 21299 tokens; 1536 reserved for the reply.
        self.assertEqual(budget, (21299 - 1536) * 3)
        over = dict(ollama)
        over["context_use_ratio"] = 0.95
        over.pop("context_chars", None)
        capped = self.router.context_char_budget(over, self.cfg["defaults"])
        self.assertEqual(capped, (int(32768 * 0.70) - 1536) * 3)

    def test_cloud_and_lmstudio_stay_disabled(self):
        by_name = {p["name"]: p for p in self.cfg["providers"]}
        for name in ("lmstudio", "groq", "gemini", "openrouter_free"):
            self.assertFalse(by_name[name]["enabled"], name)
            ok, why = self.router.provider_available(None, by_name[name], self.cfg)
            self.assertFalse(ok)
            self.assertEqual(why, "disabled")

    def test_gateway_fallback_matches_ollama(self):
        text = GATEWAY.read_text(encoding="utf-8")
        self.assertIn('base_url: "http://192.168.1.13:11434/v1"', text)
        self.assertIn('model: "qwen2.5-coder:7b"', text)


if __name__ == "__main__":
    unittest.main()
