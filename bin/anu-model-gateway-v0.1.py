#!/usr/bin/env python3
"""ANU Model Gateway v0.1 (V0.7.1) — OpenAI-compatible intelligence pipe.

Talks to LM Studio, OpenAI, or any /v1/chat/completions server.
Switch providers via config base_url + model + API key env — not code forks.

The agent must not call providers directly; this process owns outbound HTTP.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DEFAULT_CONFIG = BASE / "config" / "anu-model-gateway.yaml"


def _config_candidates():
    return (
        Path(os.environ["ANU_MODEL_GATEWAY_CONFIG"])
        if os.environ.get("ANU_MODEL_GATEWAY_CONFIG")
        else None,
        BASE / "config" / "anu-model-gateway.yaml",
        _REPO / "config" / "anu-model-gateway.yaml",
        _HERE.parent / "config" / "anu-model-gateway.yaml",
    )


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML required for model gateway config (pip install pyyaml)"
        ) from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    return data


def load_config(path: Path | None = None) -> dict:
    """Load gateway config; merge with safe defaults."""
    chosen = path
    if chosen is None:
        for candidate in _config_candidates():
            if candidate is not None and candidate.is_file():
                chosen = candidate
                break
    if chosen is None or not chosen.is_file():
        raise FileNotFoundError(
            "anu-model-gateway.yaml not found; set ANU_BASE or "
            "ANU_MODEL_GATEWAY_CONFIG"
        )

    raw = _load_yaml(chosen)
    cfg = {
        "enabled": True,
        "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "meta-llama-3.1-8b-instruct",
        "api_key_env": "ANU_MODEL_API_KEY",
        "api_key_file_env": "ANU_MODEL_API_KEY_FILE",
        "timeout_seconds": 120,
        "max_tokens": 1024,
        "temperature": 0.2,
        "privacy": {
            "default_class": "INTERNAL",
            "forbidden_classes": ["SECRET"],
        },
        "budget": {
            "max_usd_per_request": 0.05,
            "max_usd_per_day": 1.0,
            "assume_zero_cost_hosts": ["127.0.0.1", "localhost", "192.168."],
        },
        "socket": {
            "path": "/run/anu-model/model.sock",
            "mode": "0660",
        },
        "_config_path": str(chosen),
    }
    for key, value in raw.items():
        if key in ("privacy", "budget", "socket") and isinstance(value, dict):
            cfg[key] = {**cfg.get(key, {}), **value}
        else:
            cfg[key] = value
    return cfg


def resolve_api_key(cfg: dict) -> str:
    """Read API key from file env, then string env. Empty allowed for some local servers."""
    file_env = str(cfg.get("api_key_file_env") or "ANU_MODEL_API_KEY_FILE")
    key_file = os.environ.get(file_env)
    if key_file:
        path = Path(key_file)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()

    env_name = str(cfg.get("api_key_env") or "ANU_MODEL_API_KEY")
    return (os.environ.get(env_name) or "").strip()


def _normalize_base_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("base_url must be non-empty")
    return url


def _chat_url(base_url: str) -> str:
    base = _normalize_base_url(base_url)
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _models_url(base_url: str) -> str:
    base = _normalize_base_url(base_url)
    if base.endswith("/models"):
        return base
    return f"{base}/models"


def assume_zero_cost(cfg: dict) -> bool:
    host = urlparse(_normalize_base_url(cfg["base_url"])).hostname or ""
    prefixes = cfg.get("budget", {}).get("assume_zero_cost_hosts") or []
    for item in prefixes:
        token = str(item)
        if host == token or host.startswith(token.rstrip(".")):
            return True
        if token.endswith(".") and host.startswith(token):
            return True
        if token in ("192.168.", "10.", "172.16.") and host.startswith(token):
            return True
    return False


def privacy_allowed(cfg: dict, privacy_class: str | None) -> bool:
    klass = (privacy_class or cfg.get("privacy", {}).get("default_class") or "INTERNAL")
    klass = str(klass).strip().upper()
    forbidden = {
        str(x).strip().upper()
        for x in (cfg.get("privacy", {}).get("forbidden_classes") or [])
    }
    return klass not in forbidden


def request_chat(
    cfg: dict,
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    privacy_class: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """POST /v1/chat/completions. Returns provider JSON + ANU metadata."""
    if not cfg.get("enabled", True):
        raise RuntimeError("model gateway disabled in config")
    if str(cfg.get("provider") or "") != "openai_compatible":
        raise ValueError(
            f"unsupported provider {cfg.get('provider')!r}; "
            "use openai_compatible and switch via base_url"
        )
    if not privacy_allowed(cfg, privacy_class):
        raise PermissionError(
            f"privacy class {privacy_class!r} must not leave the Pi"
        )
    if not messages:
        raise ValueError("messages must be non-empty")

    key = api_key if api_key is not None else resolve_api_key(cfg)
    body = {
        "model": model or cfg["model"],
        "messages": messages,
        "max_tokens": int(
            max_tokens if max_tokens is not None else cfg.get("max_tokens", 1024)
        ),
        "temperature": float(
            temperature if temperature is not None else cfg.get("temperature", 0.2)
        ),
    }
    data = json.dumps(body).encode("utf-8")
    url = _chat_url(cfg["base_url"])
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    timeout = float(cfg.get("timeout_seconds") or 120)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"provider HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"provider unreachable: {exc.reason}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"provider returned non-JSON: {raw[:200]}") from exc

    text = extract_assistant_text(payload)
    return {
        "ok": True,
        "provider": "openai_compatible",
        "base_url": _normalize_base_url(cfg["base_url"]),
        "model": body["model"],
        "privacy_class": (
            privacy_class
            or cfg.get("privacy", {}).get("default_class")
            or "INTERNAL"
        ),
        "zero_cost": assume_zero_cost(cfg),
        "http_status": status,
        "text": text,
        "raw": payload,
    }


def extract_assistant_text(payload: dict) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    # Some servers return plain text field.
    if isinstance(first.get("text"), str):
        return first["text"]
    return ""


def health_check(cfg: dict, *, api_key: str | None = None) -> dict[str, Any]:
    """GET /v1/models — cheap liveness probe."""
    if not cfg.get("enabled", True):
        return {"ok": False, "reason": "disabled"}
    key = api_key if api_key is not None else resolve_api_key(cfg)
    url = _models_url(cfg["base_url"])
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    timeout = min(30.0, float(cfg.get("timeout_seconds") or 120))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "reason": f"HTTP {exc.code}",
            "base_url": _normalize_base_url(cfg["base_url"]),
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "reason": f"unreachable: {exc.reason}",
            "base_url": _normalize_base_url(cfg["base_url"]),
        }
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"raw": raw[:200]}
    models = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
    return {
        "ok": status == 200,
        "http_status": status,
        "base_url": _normalize_base_url(cfg["base_url"]),
        "configured_model": cfg.get("model"),
        "models": models[:50],
        "zero_cost": assume_zero_cost(cfg),
        "config_path": cfg.get("_config_path"),
    }


def request_reasoning(
    prompt: str,
    *,
    cfg: dict | None = None,
    system: str | None = None,
    privacy_class: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Provider-neutral entry used by ANU (planner may call this later)."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    config = cfg or load_config()
    messages: list[dict[str, str]] = []
    if system and system.strip():
        messages.append({"role": "system", "content": system.strip()})
    messages.append({"role": "user", "content": prompt.strip()})
    return request_chat(
        config,
        messages=messages,
        model=model,
        privacy_class=privacy_class,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Model Gateway v0.1")
    parser.add_argument(
        "--config",
        default=None,
        help="path to anu-model-gateway.yaml",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show-config", help="print resolved config (no secrets)")
    sub.add_parser("health", help="GET /v1/models")

    chat_p = sub.add_parser("chat", help="one-shot chat completion")
    chat_p.add_argument("--prompt", required=True)
    chat_p.add_argument("--system", default=None)
    chat_p.add_argument("--model", default=None)
    chat_p.add_argument("--privacy-class", default=None)

    reason_p = sub.add_parser("reason", help="request_reasoning wrapper")
    reason_p.add_argument("--prompt", required=True)
    reason_p.add_argument("--system", default=None)
    reason_p.add_argument("--privacy-class", default=None)

    args = parser.parse_args(argv)
    cfg_path = Path(args.config) if args.config else None
    cfg = load_config(cfg_path)

    if args.cmd == "show-config":
        public = {
            k: v
            for k, v in cfg.items()
            if k not in ("api_key",)
        }
        public["api_key_present"] = bool(resolve_api_key(cfg))
        print(json.dumps(public, indent=2))
        return 0

    if args.cmd == "health":
        result = health_check(cfg)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.cmd == "chat":
        messages = []
        if args.system:
            messages.append({"role": "system", "content": args.system})
        messages.append({"role": "user", "content": args.prompt})
        result = request_chat(
            cfg,
            messages=messages,
            model=args.model,
            privacy_class=args.privacy_class,
        )
        print(json.dumps({
            "ok": result["ok"],
            "model": result["model"],
            "text": result["text"],
            "zero_cost": result["zero_cost"],
        }, indent=2))
        return 0

    if args.cmd == "reason":
        result = request_reasoning(
            args.prompt,
            cfg=cfg,
            system=args.system,
            privacy_class=args.privacy_class,
        )
        print(json.dumps({
            "ok": result["ok"],
            "model": result["model"],
            "text": result["text"],
        }, indent=2))
        return 0

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
