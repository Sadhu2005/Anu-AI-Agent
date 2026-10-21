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
import signal
import socket
import struct
import sys
import threading
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
            "require_peercred": True,
            "allow_user": "anu-agent",
            "allow_group": "anu-agent",
            "max_request_bytes": 65536,
        },
        "_config_path": str(chosen),
    }
    for key, value in raw.items():
        if key in ("privacy", "budget", "socket") and isinstance(value, dict):
            cfg[key] = {**cfg.get(key, {}), **value}
        else:
            cfg[key] = value
    return cfg


def _looks_like_url(value: str) -> bool:
    text = (value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def validate_api_key(key: str) -> str:
    """Reject URL-shaped keys (common mix-up with base_url). Empty is allowed."""
    key = (key or "").strip()
    if not key:
        return ""
    if _looks_like_url(key):
        raise ValueError(
            "ANU_MODEL_API_KEY looks like a URL. Put the server address in "
            "config base_url (e.g. http://192.168.1.13:1234/v1), and put the "
            "LM Studio Manage Tokens value (or cloud API key) in "
            "ANU_MODEL_API_KEY — not the URL."
        )
    return key


def resolve_api_key(cfg: dict) -> str:
    """Read API key from file env, then string env. Empty allowed for some local servers."""
    file_env = str(cfg.get("api_key_file_env") or "ANU_MODEL_API_KEY_FILE")
    key_file = os.environ.get(file_env)
    if key_file:
        path = Path(key_file)
        if path.is_file():
            return validate_api_key(path.read_text(encoding="utf-8"))

    env_name = str(cfg.get("api_key_env") or "ANU_MODEL_API_KEY")
    return validate_api_key(os.environ.get(env_name) or "")


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


def _try_llm_router(
    messages: list[dict[str, str]],
    *,
    privacy_class: str | None = None,
    goal_id=None,
    task_id=None,
) -> dict[str, Any] | None:
    """V0.9.2: prefer multi-provider router when configured; else None."""
    router_path = None
    for candidate in (
        BASE / "bin" / "anu-llm-router-v0.1.py",
        _HERE / "anu-llm-router-v0.1.py",
        _REPO / "bin" / "anu-llm-router-v0.1.py",
    ):
        if candidate.is_file():
            router_path = candidate
            break
    if router_path is None:
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "anu_llm_router_gateway",
            router_path,
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rcfg = mod.load_config()
        if not rcfg.get("enabled") or not (rcfg.get("providers") or []):
            return None
        db = mod.db_connect()
        try:
            result = mod.chat(
                db,
                messages=messages,
                goal_id=goal_id,
                task_id=task_id,
                privacy_class=privacy_class or "INTERNAL",
                config=rcfg,
            )
        finally:
            db.close()
        return {
            "ok": True,
            "provider": result.get("provider") or "llm_router",
            "base_url": "router",
            "model": "router",
            "privacy_class": privacy_class or "INTERNAL",
            "zero_cost": False,
            "http_status": 200,
            "text": result.get("content") or "",
            "raw": result.get("raw") or result,
            "via": "llm_router",
        }
    except Exception:
        return None


def request_chat(
    cfg: dict,
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    privacy_class: str | None = None,
    api_key: str | None = None,
    tool_choice: str | None = None,
    goal_id=None,
    task_id=None,
) -> dict[str, Any]:
    """POST /v1/chat/completions. Returns provider JSON + ANU metadata.

    V0.9.2: attempts multi-provider LLM router first; falls back to the
    single-provider config so older deployments keep working.
    """
    if not cfg.get("enabled", True):
        raise RuntimeError("model gateway disabled in config")
    if not messages:
        raise ValueError("messages must be non-empty")
    if not privacy_allowed(cfg, privacy_class):
        raise PermissionError(
            f"privacy class {privacy_class!r} must not leave the Pi"
        )

    routed = _try_llm_router(
        messages,
        privacy_class=privacy_class,
        goal_id=goal_id,
        task_id=task_id,
    )
    if routed is not None:
        return routed

    if str(cfg.get("provider") or "") != "openai_compatible":
        raise ValueError(
            f"unsupported provider {cfg.get('provider')!r}; "
            "use openai_compatible and switch via base_url"
        )

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
    # OpenAI-compatible: discourage tool/function calls when planning.
    if isinstance(tool_choice, str) and tool_choice.strip():
        body["tool_choice"] = tool_choice.strip()
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


def _cli_fail(message: str, *, extra=None) -> int:
    payload = {"ok": False, "error": str(message)}
    if isinstance(extra, dict):
        payload.update(extra)
    print(json.dumps(payload, indent=2))
    return 1


# --- Unix socket server (V0.7.1 step 2) ---

MAX_REQUEST_BYTES_DEFAULT = 64 * 1024
BUFFER_SIZE = 4096
CLIENT_TIMEOUT_SEC = 30.0
_STOP = False


def peer_credentials(conn):
    """Return (pid, uid, gid) via SO_PEERCRED (Linux only)."""
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("SO_PEERCRED not available on this platform")
    size = struct.calcsize("3i")
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size)
    return struct.unpack("3i", raw)


def resolve_authorized_ids(cfg: dict):
    """Resolve allow_user / allow_group to uid/gid (Linux)."""
    sock_cfg = cfg.get("socket") or {}
    user_name = str(sock_cfg.get("allow_user") or "anu-agent")
    group_name = str(sock_cfg.get("allow_group") or "anu-agent")
    try:
        import grp
        import pwd

        uid = pwd.getpwnam(user_name).pw_uid
        gid = grp.getgrnam(group_name).gr_gid
        return uid, gid
    except Exception as exc:
        raise RuntimeError(
            f"unable to resolve allow_user={user_name!r} "
            f"allow_group={group_name!r}: {exc}"
        ) from exc


def send_json(conn, payload: dict) -> None:
    raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    conn.sendall(raw)


def read_json_line(conn, *, max_bytes: int) -> dict:
    data = bytearray()
    while len(data) < max_bytes:
        chunk = conn.recv(BUFFER_SIZE)
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in chunk:
            break
    if len(data) >= max_bytes:
        raise ValueError("request exceeds maximum size")
    line = bytes(data).split(b"\n", 1)[0]
    if not line:
        raise ValueError("empty request")
    request = json.loads(line.decode("utf-8"))
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    return request


def handle_socket_request(cfg: dict, request: dict) -> dict:
    """Dispatch a socket JSON request to health/chat/reason."""
    op = str(request.get("op") or request.get("cmd") or "").strip().lower()
    if op == "health":
        return health_check(cfg)
    if op == "chat":
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("chat requires prompt string")
        messages = []
        system = request.get("system")
        if isinstance(system, str) and system.strip():
            messages.append({"role": "system", "content": system.strip()})
        messages.append({"role": "user", "content": prompt.strip()})
        tool_choice = request.get("tool_choice")
        result = request_chat(
            cfg,
            messages=messages,
            model=request.get("model"),
            privacy_class=request.get("privacy_class"),
            tool_choice=tool_choice if isinstance(tool_choice, str) else None,
        )
        return {
            "ok": True,
            "model": result["model"],
            "text": result["text"],
            "zero_cost": result["zero_cost"],
        }
    if op == "reason":
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("reason requires prompt string")
        result = request_reasoning(
            prompt,
            cfg=cfg,
            system=request.get("system"),
            privacy_class=request.get("privacy_class"),
            model=request.get("model"),
        )
        return {
            "ok": True,
            "model": result["model"],
            "text": result["text"],
            "zero_cost": result.get("zero_cost"),
        }
    raise ValueError(f"unknown op: {op!r} (expected health|chat|reason)")


def _handle_client(conn, cfg: dict, auth_uid: int | None, auth_gid: int | None):
    sock_cfg = cfg.get("socket") or {}
    max_bytes = int(sock_cfg.get("max_request_bytes") or MAX_REQUEST_BYTES_DEFAULT)
    require_peer = bool(sock_cfg.get("require_peercred", True))
    try:
        conn.settimeout(CLIENT_TIMEOUT_SEC)
        if require_peer:
            pid, uid, gid = peer_credentials(conn)
            if auth_uid is None or auth_gid is None:
                send_json(conn, {
                    "ok": False,
                    "error": "peercred required but allow ids unresolved",
                })
                return
            if uid != auth_uid or gid != auth_gid:
                send_json(conn, {
                    "ok": False,
                    "error": "Peer credentials rejected.",
                    "peer": {"pid": pid, "uid": uid, "gid": gid},
                })
                return
        request = read_json_line(conn, max_bytes=max_bytes)
        result = handle_socket_request(cfg, request)
        send_json(conn, result)
    except Exception as exc:
        try:
            send_json(conn, {"ok": False, "error": str(exc)})
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def serve_unix_socket(cfg: dict, *, socket_path: str | None = None) -> None:
    """Long-running AF_UNIX listener for agent clients."""
    global _STOP
    _STOP = False
    sock_cfg = cfg.get("socket") or {}
    path = Path(socket_path or sock_cfg.get("path") or "/run/anu-model/model.sock")
    mode_raw = sock_cfg.get("mode") or "0660"
    mode = int(str(mode_raw), 8) if not isinstance(mode_raw, int) else mode_raw
    require_peer = bool(sock_cfg.get("require_peercred", True))

    auth_uid = auth_gid = None
    if require_peer:
        auth_uid, auth_gid = resolve_authorized_ids(cfg)

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass

    def _shutdown(signum, frame):
        global _STOP
        _STOP = True

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if not hasattr(socket, "AF_UNIX"):
        raise RuntimeError("AF_UNIX not available on this platform")

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    server.listen(8)
    server.settimeout(1.0)
    print(f"ANU Model Gateway listening on {path}", flush=True)

    try:
        while not _STOP:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            thread = threading.Thread(
                target=_handle_client,
                args=(conn, cfg, auth_uid, auth_gid),
                daemon=True,
            )
            thread.start()
    finally:
        server.close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass


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

    serve_p = sub.add_parser(
        "serve",
        help="listen on Unix socket for agent clients",
    )
    serve_p.add_argument(
        "--socket",
        default=None,
        help="override socket path from config",
    )
    serve_p.add_argument(
        "--no-peercred",
        action="store_true",
        help="disable SO_PEERCRED check (tests / non-Linux only)",
    )

    args = parser.parse_args(argv)
    try:
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

        if args.cmd == "serve":
            if args.no_peercred:
                cfg.setdefault("socket", {})
                cfg["socket"] = {**cfg["socket"], "require_peercred": False}
            serve_unix_socket(cfg, socket_path=args.socket)
            return 0

        parser.error(f"unknown command: {args.cmd}")
        return 2
    except (ValueError, PermissionError, RuntimeError, FileNotFoundError) as exc:
        return _cli_fail(exc)


if __name__ == "__main__":
    sys.exit(main())
