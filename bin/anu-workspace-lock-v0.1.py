#!/usr/bin/env python3
"""ANU Workspace Lock v0.1 — exactly one active writer per workspace."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def lock_path(root) -> Path:
    return Path(root) / ".anu" / "WRITER.lock"


def acquire(root, *, owner: str, ttl_seconds: int = 3600) -> dict:
    root = Path(root)
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if float(data.get("expires_at") or 0) > now and data.get("owner") != owner:
                return {
                    "ok": False,
                    "error": "workspace_locked",
                    "holder": data.get("owner"),
                    "expires_at": data.get("expires_at"),
                }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    payload = {
        "owner": owner,
        "acquired_at": now,
        "expires_at": now + int(ttl_seconds),
        "pid": os.getpid(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {"ok": True, "lock": payload, "path": str(path)}


def release(root, *, owner: str) -> dict:
    path = lock_path(root)
    if not path.is_file():
        return {"ok": True, "released": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("owner") not in (None, owner):
            return {"ok": False, "error": "not_owner", "holder": data.get("owner")}
        path.unlink(missing_ok=True)
        return {"ok": True, "released": True}
    except (OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return {"ok": True, "released": True, "forced": True}
