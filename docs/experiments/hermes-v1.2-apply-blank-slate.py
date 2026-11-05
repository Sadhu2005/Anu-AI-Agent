#!/usr/bin/env python3
"""Apply V1.2 blank-slate Hermes config on the Pi (no ANU secrets).

Run on the Pi as the Hermes owner (anu-vps1947), not anu-agent:

  python3 hermes-v1.2-apply-blank-slate.py

Does not write real API keys. Inserts empty placeholders in ~/.hermes/.env
and rewrites blank-slate tool policy in ~/.hermes/config.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML required") from exc


HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
CONFIG = HERMES_HOME / "config.yaml"
ENV = HERMES_HOME / ".env"

# Operator fills these manually on the Pi. Never commit real values.
ENV_PLACEHOLDERS = {
    "OPENROUTER_API_KEY": "",
    "HERMES_OPENROUTER_API_KEY": "",
    "HERMES_OPENROUTER_MODEL": "openrouter/auto",
    "LM_BASE_URL": "http://192.168.1.13:1234/v1",
    "HERMES_LMSTUDIO_BASE_URL": "http://192.168.1.13:1234/v1",
    "LM_API_KEY": "",
    "HERMES_LMSTUDIO_MODEL": "",
}

BLANK_CLI_TOOLSETS = ["file", "terminal"]

DISABLED_TOOLSETS = [
    "web",
    "browser",
    "code_execution",
    "vision",
    "video",
    "image_gen",
    "video_gen",
    "x_search",
    "tts",
    "stt",
    "skills",
    "todo",
    "kanban",
    "memory",
    "context_engine",
    "session_search",
    "connections",
    "clarify",
    "delegation",
    "cronjob",
    "discord",
    "discord_admin",
    "yuanbao",
    "computer_use",
]


def upsert_env(path: Path, values: dict[str, str]) -> None:
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    index = {
        line.split("=", 1)[0]: i
        for i, line in enumerate(lines)
        if "=" in line and not line.strip().startswith("#")
    }
    for key, val in values.items():
        rendered = f"{key}={val}"
        if key in index:
            # Preserve an already-filled non-empty secret.
            existing = lines[index[key]].split("=", 1)[1] if "=" in lines[index[key]] else ""
            if existing.strip() and not val:
                continue
            lines[index[key]] = rendered
        else:
            lines.append(rendered)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def apply_config(path: Path) -> None:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(cfg, dict):
        cfg = {}

    model = dict(cfg.get("model") or {})
    # Default to OpenRouter once OPENROUTER_API_KEY is set by operator.
    # For LM Studio smoke: hermes config set model.provider lmstudio
    #   and ensure LM_BASE_URL / HERMES_LMSTUDIO_MODEL are set.
    model["provider"] = "openrouter"
    model["default"] = "openrouter/auto"
    model["base_url"] = "https://openrouter.ai/api/v1"
    cfg["model"] = model

    pt = dict(cfg.get("platform_toolsets") or {})
    pt["cli"] = list(BLANK_CLI_TOOLSETS)
    cfg["platform_toolsets"] = pt

    agent = dict(cfg.get("agent") or {})
    agent["disabled_toolsets"] = list(DISABLED_TOOLSETS)
    cfg["agent"] = agent

    for section in ("telegram", "discord", "slack", "whatsapp", "signal", "qqbot"):
        block = cfg.get(section)
        if isinstance(block, dict):
            block["enabled"] = False
            cfg[section] = block

    for key in ("compression", "context_compression"):
        block = cfg.get(key)
        if isinstance(block, dict) and "enabled" in block:
            block["enabled"] = False
            cfg[key] = block

    memory = dict(cfg.get("memory") or {})
    memory["memory_enabled"] = False
    memory["user_profile_enabled"] = False
    cfg["memory"] = memory

    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def main() -> int:
    if not HERMES_HOME.is_dir():
        raise SystemExit(f"HERMES_HOME missing: {HERMES_HOME}")
    upsert_env(ENV, ENV_PLACEHOLDERS)
    apply_config(CONFIG)
    print(f"OK blank-slate applied under {HERMES_HOME}")
    print("Manual next steps (operator):")
    print(f"  1. Edit {ENV}")
    print("     OPENROUTER_API_KEY=sk-or-...   # cloud smoke")
    print("     HERMES_LMSTUDIO_MODEL=<id>     # from LM Studio /v1/models")
    print("  2. Start LM Studio OpenAI-compatible server on the PC (you own that service).")
    print("  3. Run: bash hermes-v1.2-smoke.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
