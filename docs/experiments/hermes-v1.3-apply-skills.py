#!/usr/bin/env python3
"""Apply the anu-hermes skill set: blank-slate tools, Podman air gap, Ollama.

Order inside one write: toolsets, then terminal isolation, then the model.
That keeps OpenRouter from replacing Qwen and keeps skills disabled.

Does not read /srv/anu/secrets or another user's ~/.hermes/.env.
The API key written here is the Ollama placeholder "ollama", not a secret.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path


HERE = Path(__file__).resolve().parent
OLLAMA_BASE_URL = "http://192.168.1.13:11434/v1"
OLLAMA_MODEL = "qwen2.5-coder:7b"
OLLAMA_API_KEY_PLACEHOLDER = "ollama"
# lmstudio is the Hermes provider that accepts an explicit context below its
# 64K floor. Qwen2.5-Coder's GGUF window is 32768; 65% of that is 21299.
OLLAMA_PROVIDER = "lmstudio"
OLLAMA_WINDOW_TOKENS = 32768
OLLAMA_NUM_CTX = 21299

MESSAGING = ("telegram", "discord", "slack", "whatsapp", "signal", "qqbot")


def _load(name: str, mod_name: str):
    path = HERE / name
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def apply_skillset(cfg: dict) -> dict:
    """Return config with the locked skill set and local model."""
    blank = _load("hermes-v1.2-apply-blank-slate.py", "hermes_blank_slate_v13")
    isolation = _load("hermes-v1.3-isolation.py", "hermes_isolation_skills_v13")
    if not isinstance(cfg, dict):
        raise isolation.IsolationError("config must be a mapping")

    out = dict(cfg)
    toolsets = dict(out.get("platform_toolsets") or {})
    toolsets["cli"] = list(blank.BLANK_CLI_TOOLSETS)
    out["platform_toolsets"] = toolsets

    agent = dict(out.get("agent") or {})
    agent["disabled_toolsets"] = list(blank.DISABLED_TOOLSETS)
    out["agent"] = agent

    for section in MESSAGING:
        block = out.get(section)
        if isinstance(block, dict):
            block = dict(block)
            block["enabled"] = False
            out[section] = block

    for key in ("compression", "context_compression"):
        block = out.get(key)
        if isinstance(block, dict) and "enabled" in block:
            block = dict(block)
            block["enabled"] = False
            out[key] = block

    memory = dict(out.get("memory") or {})
    memory["memory_enabled"] = False
    memory["user_profile_enabled"] = False
    out["memory"] = memory

    # Isolation after toolsets. Model override after isolation so a previous
    # OpenRouter block cannot win.
    out = isolation.apply_terminal_isolation(out)
    model = dict(out.get("model") or {})
    model["provider"] = OLLAMA_PROVIDER
    model["default"] = OLLAMA_MODEL
    model["base_url"] = OLLAMA_BASE_URL
    model["api_key"] = OLLAMA_API_KEY_PLACEHOLDER
    model["context_length"] = OLLAMA_NUM_CTX
    model["ollama_num_ctx"] = OLLAMA_NUM_CTX
    out["model"] = model
    return out


def apply_hermes_home(hermes_home: Path) -> None:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML required") from exc

    isolation = _load("hermes-v1.3-isolation.py", "hermes_isolation_skills_write")
    config_path = hermes_home / "config.yaml"
    cfg = {}
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        cfg = loaded if isinstance(loaded, dict) else {}
    updated = apply_skillset(cfg)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(updated, sort_keys=False), encoding="utf-8")
    env_path = hermes_home / ".env"
    isolation._upsert_env_line(env_path, "HERMES_DOCKER_BINARY", "podman")
    isolation._upsert_env_line(env_path, "HERMES_OLLAMA_API_KEY", OLLAMA_API_KEY_PLACEHOLDER)
    isolation._upsert_env_line(env_path, "OPENAI_API_KEY", OLLAMA_API_KEY_PLACEHOLDER)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply anu-hermes skill set")
    parser.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", ""))
    args = parser.parse_args(argv)
    home = Path(args.hermes_home or (Path.home() / ".hermes"))
    home.mkdir(parents=True, exist_ok=True)
    apply_hermes_home(home)
    print(f"OK skill set applied under {home}")
    print(f"  toolsets: file, terminal")
    print(f"  model: {OLLAMA_MODEL} @ {OLLAMA_BASE_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
