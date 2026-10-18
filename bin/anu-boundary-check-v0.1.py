#!/usr/bin/env python3
"""ANU Boundary Self-Check v0.1 (V0.6.9).

Probes filesystem boundaries the agent is expected to honor under systemd.

```text
read-only paths  → probe write must FAIL
writable paths   → probe write must SUCCEED (then remove)
```

Fail closed: callers must not schedule autonomous work when ``ok`` is false.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DB_FILE = BASE / "brain" / "anu-brain.db"

DEFAULT_READ_ONLY = (
    "bin",
    "config",
    "src",
    "secrets",
    "tests",
)
DEFAULT_WRITABLE = (
    "home",
    "brain",
    "workspace",
    "sandbox",
    "browser",
    "logs",
    "runtime",
    "checkpoints",
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _probe_write(directory: Path):
    """Attempt a create+unlink under ``directory``. Return (ok, detail)."""
    directory = Path(directory)
    if not directory.exists():
        # Missing path is not a write success; treat as soft fail for RO
        # expectations and hard fail for RW expectations (caller decides).
        return False, "missing"
    marker = directory / f".anu-boundary-probe-{uuid.uuid4().hex}"
    try:
        marker.write_text("probe\n", encoding="utf-8")
    except OSError as exc:
        return False, f"{type(exc).__name__}:{exc.errno}"
    try:
        marker.unlink()
    except OSError as exc:
        return False, f"cleanup:{type(exc).__name__}:{exc.errno}"
    return True, "ok"


def run_boundary_check(
    base=None,
    read_only=None,
    writable=None,
):
    """Return a result dict with per-path outcomes and overall ``ok``."""
    root = Path(base) if base else BASE
    ro_names = list(read_only) if read_only is not None else list(DEFAULT_READ_ONLY)
    rw_names = list(writable) if writable is not None else list(DEFAULT_WRITABLE)

    results = []
    ok = True

    for name in ro_names:
        path = root / name
        wrote, detail = _probe_write(path)
        # Read-only: write must fail.
        path_ok = not wrote
        if not path_ok:
            ok = False
        results.append({
            "path": str(path),
            "expect": "read_only",
            "write_succeeded": wrote,
            "detail": detail,
            "ok": path_ok,
        })

    for name in rw_names:
        path = root / name
        wrote, detail = _probe_write(path)
        path_ok = wrote
        if not path_ok:
            ok = False
        results.append({
            "path": str(path),
            "expect": "writable",
            "write_succeeded": wrote,
            "detail": detail,
            "ok": path_ok,
        })

    return {
        "ok": ok,
        "checked_at": now_iso(),
        "base": str(root),
        "results": results,
    }


def record_boundary_event(db, result):
    """Persist BOUNDARY_CHECK into agent_events when the table exists."""
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide execute()")
    try:
        db.execute(
            """
            INSERT INTO agent_events (event_type, message, metadata)
            VALUES (?, ?, ?)
            """,
            (
                "BOUNDARY_CHECK",
                "ok" if result.get("ok") else "BOUNDARY_FAIL",
                json.dumps(result, sort_keys=True),
            ),
        )
        db.commit()
        return True
    except sqlite3.OperationalError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Boundary Self-Check v0.1")
    parser.add_argument("--base", default=str(BASE))
    parser.add_argument("--db", default=str(DB_FILE))
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args(argv)

    result = run_boundary_check(base=args.base)
    if args.record:
        try:
            db = sqlite3.connect(args.db)
            record_boundary_event(db, result)
            db.close()
        except Exception as exc:
            result["record_error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
