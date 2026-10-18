#!/usr/bin/env python3
"""ANU Capability Adapter v0.1 (V0.6.2 follow-up).

Agent / Safe Mutation execution goes through the capability registry
instead of calling the command gateway directly.

```text
operation name → capability name → evaluate_request → invoke → gateway run
```
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"

# Gateway / Safe Mutation operation names → registry capability names.
# Prefer dotted aliases when registered; fall back to command name.
OPERATION_TO_CAPABILITY = {
    "python_test": "python.test",
    "python_compile": "python.compile",
    "git_status": "git.status",
    "git_diff": "git.diff",
    "git_status_short": "git_status_short",
    "git_diff_stat": "git_diff_stat",
}


def _module_path(name):
    for candidate in (
        BASE / "bin" / name,
        _HERE / name,
        _REPO / "bin" / name,
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"module not found: {name}")


def _load_registry_module():
    path = _module_path("anu-capability-registry-v0.1.py")
    spec = importlib.util.spec_from_file_location(
        "anu_capability_registry_v0_1_adapter",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_db_helper():
    try:
        path = _module_path("anu-db-v0.1.py")
    except FileNotFoundError:
        return None
    spec = importlib.util.spec_from_file_location(
        "anu_db_v0_1_adapter",
        path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def capability_name_for_operation(operation):
    if not isinstance(operation, str) or not operation.strip():
        raise ValueError("operation must be a non-empty string")
    op = operation.strip()
    return OPERATION_TO_CAPABILITY.get(op, op)


def open_brain(db_path=None):
    """Best-effort Brain connection for capability audit rows."""
    path = Path(db_path) if db_path is not None else DB_FILE
    if not path.is_file():
        return None
    helper = _load_db_helper()
    if helper is not None:
        return helper.db_connect(path)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def run_capability(
    project_name,
    operation,
    *,
    task_id=None,
    goal_id=None,
    db=None,
    registry=None,
    close_db=False,
):
    """Invoke a named operation via the capability registry.

    Returns a dict compatible with Safe Mutation ``run_gateway``::

        process_exit_code, result, stdout, stderr

    plus ``capability`` (full registry invoke payload) and ``admitted``.
    """
    if not isinstance(project_name, str) or not project_name.strip():
        raise ValueError("project_name must be a non-empty string")

    own_db = False
    if db is None:
        db = open_brain()
        own_db = db is not None

    try:
        if registry is None:
            reg_mod = _load_registry_module()
            registry = reg_mod.CapabilityRegistry.from_files()

        name = capability_name_for_operation(operation)
        # If dotted alias is not registered, fall back to bare operation.
        if registry.get(name) is None and name != operation:
            name = operation.strip()

        payload = registry.invoke(
            name,
            {"project": project_name.strip(), "task_id": task_id},
            db=db,
            task_id=task_id,
            goal_id=goal_id,
        )

        if not payload.get("admitted"):
            reason = (payload.get("decision") or {}).get("reason") or "capability denied"
            return {
                "process_exit_code": 1,
                "result": {
                    "ok": False,
                    "error": reason,
                    "capability_denied": True,
                    "denial_class": (payload.get("decision") or {}).get(
                        "denial_class"
                    ),
                },
                "stdout": "",
                "stderr": "",
                "admitted": False,
                "capability": payload,
            }

        gateway_result = payload.get("gateway_result")
        exit_code = payload.get("gateway_exit_code")
        if exit_code is None:
            exit_code = 0 if payload.get("ok") else 1

        return {
            "process_exit_code": int(exit_code),
            "result": gateway_result,
            "stdout": (payload.get("stdout") or "")[-20000:],
            "stderr": (payload.get("stderr") or "")[-12000:],
            "admitted": True,
            "capability": payload,
        }
    finally:
        if (own_db or close_db) and db is not None:
            try:
                db.close()
            except Exception:
                pass


def capability_ok(run):
    """True when capability admitted and gateway reported ok."""
    if not run.get("admitted", True):
        return False
    result = run.get("result") or {}
    return run.get("process_exit_code") == 0 and result.get("ok") is True


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ANU Capability Adapter v0.1"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--task-id", type=int, default=None)
    parser.add_argument("--goal-id", type=int, default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)

    db = open_brain(args.db) if args.db or DB_FILE.is_file() else None
    try:
        run = run_capability(
            args.project,
            args.operation,
            task_id=args.task_id,
            goal_id=args.goal_id,
            db=db,
        )
    finally:
        if db is not None:
            db.close()

    print(json.dumps({"ok": capability_ok(run), "run": run}, indent=2))
    return 0 if capability_ok(run) else 1


if __name__ == "__main__":
    sys.exit(main())
