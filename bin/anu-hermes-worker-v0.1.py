#!/usr/bin/env python3
"""ANU Hermes worker v0.1 — prepare an isolated job dir. Does not spawn Hermes."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_ISOLATION_PATH = _HERE.parent / "docs" / "experiments" / "hermes-v1.3-isolation.py"
_RESULT_PATH = _HERE / "anu-hermes-result-v0.1.py"
_DENIED_PARTS = ("TOKEN", "SECRET", "KEY", "GITHUB", "ANU_")
_isolation = None
_result = None


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolation():
    global _isolation
    if _isolation is None:
        _isolation = _load(_ISOLATION_PATH, "anu_hermes_isolation_v14")
    return _isolation


def result_mod():
    global _result
    if _result is None:
        _result = _load(_RESULT_PATH, "anu_hermes_result_v14")
    return _result


def worker_env(parent: dict | None = None) -> dict:
    """Allow only the Podman binary name. Drop credential-shaped variables."""
    source = dict(parent) if parent is not None else dict(os.environ)
    for key in list(source):
        upper = str(key).upper()
        if any(part in upper for part in _DENIED_PARTS):
            source.pop(key, None)
    if "HERMES_DOCKER_BINARY" in source and source["HERMES_DOCKER_BINARY"] != "podman":
        source.pop("HERMES_DOCKER_BINARY", None)
    return {"HERMES_DOCKER_BINARY": "podman"}


def _failed(job_id: str, reason: str) -> dict:
    return {
        "ok": False,
        "job_id": job_id,
        "status": "failed",
        "reason": reason,
        "changed_files": [],
        "files": [],
        "commit": False,
        "push": False,
        "env": worker_env({}),
    }


def run_job(
    request: dict,
    *,
    jobs_root,
    runner=None,
    brain_path=None,
    parent_env: dict | None = None,
    source=None,
) -> dict:
    """Prepare a job workspace and accept only an injected runner result.

    brain_path is never opened. Live Hermes execution stays disabled.
    """
    del brain_path  # caller may pass a sentinel; this function must not write it
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    job_id = str(request.get("job_id") or "")
    if request.get("git_push") or request.get("production_access") or request.get("network_access"):
        raise ValueError("job refuses git_push, production_access, or network_access")
    env = worker_env(parent_env)
    iso = isolation()
    workspace = iso.prepare_job_workspace(jobs_root, job_id, source)
    if runner is None:
        failed = _failed(job_id, "live_execution_disabled")
        failed["env"] = env
        failed["workspace"] = str(workspace)
        return failed
    try:
        payload = runner(workspace, env)
    except Exception as exc:
        failed = _failed(job_id, f"runner_error:{type(exc).__name__}")
        failed["env"] = env
        failed["workspace"] = str(workspace)
        return failed
    if not isinstance(payload, dict) or payload.get("status") == "killed":
        failed = _failed(job_id, "killed")
        failed["env"] = env
        failed["workspace"] = str(workspace)
        return failed
    normalized = result_mod().normalize_result(payload, workspace)
    normalized["env"] = env
    normalized["workspace"] = str(workspace)
    normalized["commit"] = False
    normalized["push"] = False
    return normalized


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ANU Hermes worker v0.1")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print the disabled-execution flag and exit. Does not spawn Hermes.",
    )
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps({"ok": True, "execute": False, "spawn": False}))
        return 0
    print(json.dumps({"ok": False, "reason": "live_execution_disabled"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
