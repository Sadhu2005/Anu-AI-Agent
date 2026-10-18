#!/usr/bin/env python3
"""ANU Model Gateway client v0.1 — agent-side AF_UNIX caller.

anu-agent stays PrivateNetwork and speaks only to the model gateway socket.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DEFAULT_SOCKET = "/run/anu-model/model.sock"
MAX_RESPONSE_BYTES = 256 * 1024


def resolve_socket_path(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("ANU_MODEL_SOCKET")
    if env:
        return env
    # Best-effort: read config path if present.
    for candidate in (
        BASE / "config" / "anu-model-gateway.yaml",
        Path(__file__).resolve().parents[1] / "config" / "anu-model-gateway.yaml",
    ):
        if not candidate.is_file():
            continue
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            path = ((data.get("socket") or {}).get("path"))
            if path:
                return str(path)
        except Exception:
            pass
    return DEFAULT_SOCKET


def call_gateway(
    op: str,
    *,
    socket_path: str | None = None,
    timeout: float = 130.0,
    **fields,
) -> dict:
    """Send one JSON line request; return parsed JSON response."""
    if not hasattr(socket, "AF_UNIX"):
        raise RuntimeError("AF_UNIX not available on this platform")
    path = resolve_socket_path(socket_path)
    request = {"op": op, **fields}
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        data = bytearray()
        while len(data) < MAX_RESPONSE_BYTES:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if b"\n" in chunk:
                break
        if not data:
            raise RuntimeError("model gateway returned no response")
        line = bytes(data).split(b"\n", 1)[0]
        result = json.loads(line.decode("utf-8"))
        if not isinstance(result, dict):
            raise RuntimeError("model gateway response must be a JSON object")
        return result
    finally:
        sock.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Model Gateway Client v0.1")
    parser.add_argument(
        "--socket",
        default=None,
        help="Unix socket path (default from config / ANU_MODEL_SOCKET)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health")
    chat_p = sub.add_parser("chat")
    chat_p.add_argument("--prompt", required=True)
    chat_p.add_argument("--system", default=None)
    chat_p.add_argument("--model", default=None)
    reason_p = sub.add_parser("reason")
    reason_p.add_argument("--prompt", required=True)
    reason_p.add_argument("--system", default=None)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "health":
            result = call_gateway("health", socket_path=args.socket)
        elif args.cmd == "chat":
            fields = {"prompt": args.prompt}
            if args.system:
                fields["system"] = args.system
            if args.model:
                fields["model"] = args.model
            result = call_gateway("chat", socket_path=args.socket, **fields)
        elif args.cmd == "reason":
            fields = {"prompt": args.prompt}
            if args.system:
                fields["system"] = args.system
            result = call_gateway("reason", socket_path=args.socket, **fields)
        else:
            parser.error(f"unknown command: {args.cmd}")
            return 2
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
