#!/usr/bin/env python3
"""ANU Resource Policy v0.1 (V0.6.6).

Single source of truth for host resource limits and task admission:

```text
config/anu-resources.yaml  →  load_resource_policy()
       ↓
Guardian / Governor / Task Engine / Scheduler
```

Also provides temperature hysteresis helpers and RUN|WAIT|DENY admission.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent

STATE_FILE = BASE / "runtime" / "anu-guardian-state.json"

DECISION_RUN = "RUN"
DECISION_WAIT = "WAIT"
DECISION_DENY = "DENY"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _resolve_config(name: str) -> Path:
    for candidate in (
        BASE / "config" / name,
        _REPO / "config" / name,
        _HERE.parent / "config" / name,
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(name)


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_resource_policy(path=None):
    """Load authoritative ``anu-resources.yaml`` and normalize thresholds."""
    cfg_path = Path(path) if path else _resolve_config("anu-resources.yaml")
    raw = load_yaml(cfg_path)
    rp = raw.get("resource_policy") or {}
    temp = rp.get("temperature") or {}
    memory = rp.get("memory") or {}
    storage = rp.get("storage") or {}
    execution = rp.get("execution") or {}
    agent = rp.get("agent") or {}
    cpu = rp.get("cpu") or {}

    policy = {
        "source": str(cfg_path),
        "raw": raw,
        "warning_celsius": float(temp.get("warning_celsius", 60)),
        "pause_celsius": float(temp.get("pause_celsius", 65)),
        "resume_celsius": float(temp.get("resume_celsius", 55)),
        "emergency_free_mb": float(memory.get("emergency_free_mb", 1024)),
        "minimum_free_ram_mb": float(
            memory.get("minimum_free_ram_mb", 2048)
        ),
        "max_memory_mb": float(memory.get("max_mb", 3072)),
        "minimum_free_gb": float(storage.get("minimum_free_gb", 50)),
        "max_cpu_percent": float(cpu.get("max_percent", 50)),
        "max_parallel_tasks": int(agent.get("max_parallel_tasks", 1)),
        "max_attempts_per_task": int(
            execution.get("max_attempts_per_task", 3)
        ),
        "heavy_workload_ram_threshold_mb": float(
            execution.get("heavy_workload_ram_threshold_mb", 1024)
        ),
        "heavy_workload_cpu_threshold_percent": float(
            execution.get("heavy_workload_cpu_threshold_percent", 60)
        ),
        "heavy_profile_ram_mb": float(
            execution.get("heavy_profile_ram_mb", 512)
        ),
        "production_priority": (rp.get("production") or {}).get(
            "priority", "highest"
        ),
    }
    return policy


def load_guardian_state(path=None):
    state_path = Path(path) if path else STATE_FILE
    if not state_path.is_file():
        return {"status": None, "updated_at": None}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"status": None, "updated_at": None}
        return data
    except (OSError, json.JSONDecodeError):
        return {"status": None, "updated_at": None}


def save_guardian_state(status, path=None):
    state_path = Path(path) if path else STATE_FILE
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": status, "updated_at": now_iso()}
    state_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def determine_host_status(
    policy,
    *,
    memory_available_mb,
    temperature_c,
    disk_free_gb,
    previous_status=None,
):
    """Return NORMAL | WARNING | PAUSE with temperature hysteresis.

    After WARNING/PAUSE, status returns to NORMAL only when temperature is
    strictly below ``resume_celsius`` (and other hard limits are clear).
    """
    warning = policy["warning_celsius"]
    pause = policy["pause_celsius"]
    resume = policy["resume_celsius"]
    emergency_ram = policy["emergency_free_mb"]
    min_disk = policy["minimum_free_gb"]

    if memory_available_mb is not None and memory_available_mb < emergency_ram:
        return "PAUSE"

    if disk_free_gb is not None and disk_free_gb < min_disk:
        return "PAUSE"

    temp = temperature_c
    elevated = previous_status in ("WARNING", "PAUSE")

    if temp is not None:
        if temp >= pause:
            return "PAUSE"
        if elevated and temp >= resume:
            # Still warm: stay elevated (WARNING band) until below resume.
            return "WARNING"
        if temp >= warning:
            return "WARNING"

    return "NORMAL"


def assert_policy_consistency(
    resources_path=None,
    admission_path=None,
    policy_path=None,
):
    """Fail if duplicate YAML sources disagree with the authoritative file.

    Closes SECURITY G5 for the numeric resource limits that must agree.
    """
    auth = load_resource_policy(resources_path)
    errors = []

    try:
        admission = load_yaml(
            Path(admission_path)
            if admission_path
            else _resolve_config("anu-admission-policy.yaml")
        )
    except FileNotFoundError as exc:
        errors.append(str(exc))
        admission = {}

    try:
        policy_yaml = load_yaml(
            Path(policy_path)
            if policy_path
            else _resolve_config("anu-policy.yaml")
        )
    except FileNotFoundError as exc:
        errors.append(str(exc))
        policy_yaml = {}

    host = admission.get("host_safety") or {}
    checks = [
        (
            "host_safety.warning_temperature_c",
            host.get("warning_temperature_c"),
            auth["warning_celsius"],
        ),
        (
            "host_safety.pause_temperature_c",
            host.get("pause_temperature_c"),
            auth["pause_celsius"],
        ),
        (
            "host_safety.minimum_free_disk_gb",
            host.get("minimum_free_disk_gb"),
            auth["minimum_free_gb"],
        ),
        (
            "host_safety.minimum_free_ram_mb",
            host.get("minimum_free_ram_mb"),
            auth["minimum_free_ram_mb"],
        ),
    ]
    exec_adm = admission.get("execution") or {}
    checks.extend(
        [
            (
                "execution.heavy_workload_ram_threshold_mb",
                exec_adm.get("heavy_workload_ram_threshold_mb"),
                auth["heavy_workload_ram_threshold_mb"],
            ),
            (
                "execution.heavy_workload_cpu_threshold_percent",
                exec_adm.get("heavy_workload_cpu_threshold_percent"),
                auth["heavy_workload_cpu_threshold_percent"],
            ),
        ]
    )
    budget = admission.get("anu_budget") or {}
    checks.append(
        (
            "anu_budget.maximum_ram_mb",
            budget.get("maximum_ram_mb"),
            auth["max_memory_mb"],
        )
    )

    res = policy_yaml.get("resources") or {}
    checks.extend(
        [
            (
                "anu-policy.resources.max_memory_mb",
                res.get("max_memory_mb"),
                auth["max_memory_mb"],
            ),
            (
                "anu-policy.resources.temperature_limit_celsius",
                res.get("temperature_limit_celsius"),
                auth["pause_celsius"],
            ),
            (
                "anu-policy.resources.max_cpu_percent",
                res.get("max_cpu_percent"),
                auth["max_cpu_percent"],
            ),
            (
                "anu-policy.resources.max_parallel_tasks",
                res.get("max_parallel_tasks"),
                auth["max_parallel_tasks"],
            ),
        ]
    )
    exec_pol = policy_yaml.get("execution") or {}
    checks.append(
        (
            "anu-policy.execution.max_attempts_per_task",
            exec_pol.get("max_attempts_per_task"),
            auth["max_attempts_per_task"],
        )
    )

    for label, found, expected in checks:
        if found is None:
            errors.append(f"{label}: missing (expected {expected})")
            continue
        try:
            if float(found) != float(expected):
                errors.append(
                    f"{label}: {found} != authoritative {expected}"
                )
        except (TypeError, ValueError):
            errors.append(f"{label}: unreadable value {found!r}")

    return {
        "ok": len(errors) == 0,
        "authoritative": auth["source"],
        "errors": errors,
    }


def estimate_task_ram_mb(operation=None, profile_ram_mb=None):
    """Estimate RAM for a task from profile or known heavy operations."""
    if profile_ram_mb is not None:
        return float(profile_ram_mb)
    op = str(operation or "").strip()
    if op in ("python_test", "python_compile"):
        return 512.0
    return 128.0


def is_heavy_task(policy, *, operation=None, profile_ram_mb=None):
    estimate = estimate_task_ram_mb(operation, profile_ram_mb)
    return estimate >= float(policy["heavy_profile_ram_mb"])


def admit_task(
    policy,
    *,
    host_status,
    memory_available_mb,
    disk_free_gb=None,
    temperature_c=None,
    operation=None,
    profile_ram_mb=None,
    wait_seconds=120,
):
    """Per-task admission: RUN | WAIT | DENY.

    - Disk below minimum → DENY
    - Host PAUSE / WARNING (hot) → WAIT (re-wake when Guardian returns NORMAL)
    - Heavy task when available RAM below heavy threshold → WAIT
    """
    if disk_free_gb is not None and disk_free_gb < policy["minimum_free_gb"]:
        return {
            "decision": DECISION_DENY,
            "reason": "disk_low",
            "retry_after": None,
        }

    status = str(host_status or "").upper()
    if status in ("PAUSE", "WARNING"):
        return {
            "decision": DECISION_WAIT,
            "reason": f"host_{status.lower()}",
            "retry_after": (
                datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
            ).isoformat(),
        }

    heavy = is_heavy_task(
        policy, operation=operation, profile_ram_mb=profile_ram_mb
    )
    if heavy and memory_available_mb is not None:
        if memory_available_mb < policy["heavy_workload_ram_threshold_mb"]:
            return {
                "decision": DECISION_WAIT,
                "reason": "heavy_low_ram",
                "retry_after": (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=wait_seconds)
                ).isoformat(),
                "heavy": True,
            }

    return {
        "decision": DECISION_RUN,
        "reason": "ok",
        "retry_after": None,
        "heavy": heavy,
    }


def max_attempts(policy=None):
    if policy is None:
        policy = load_resource_policy()
    return int(policy["max_attempts_per_task"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="ANU Resource Policy v0.1")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show")
    sub.add_parser("check-consistency")

    admit = sub.add_parser("admit")
    admit.add_argument("--status", default="NORMAL")
    admit.add_argument("--available-mb", type=float, required=True)
    admit.add_argument("--disk-free-gb", type=float, default=None)
    admit.add_argument("--operation", default="python_test")

    args = parser.parse_args(argv)
    policy = load_resource_policy()

    if args.cmd == "show":
        out = {k: v for k, v in policy.items() if k != "raw"}
        print(json.dumps({"ok": True, "policy": out}, indent=2))
        return 0
    if args.cmd == "check-consistency":
        result = assert_policy_consistency()
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "admit":
        decision = admit_task(
            policy,
            host_status=args.status,
            memory_available_mb=args.available_mb,
            disk_free_gb=args.disk_free_gb,
            operation=args.operation,
        )
        print(json.dumps({"ok": True, "admission": decision}, indent=2))
        return 0
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
