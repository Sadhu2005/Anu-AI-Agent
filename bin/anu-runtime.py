#!/usr/bin/env python3

import importlib.util
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent

GUARDIAN = BASE / "bin" / "anu-resource-guardian.py"
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"
RECOVERY = BASE / "bin" / "anu-recovery-v0.1.py"
STARTUP = BASE / "bin" / "anu-startup-recovery-v0.1.py"
DB_FILE = BASE / "brain" / "anu-brain.db"
SCHEDULER_LOCK_FILE = BASE / "runtime" / "anu-scheduler.lock"
EXECUTION_LOCK_FILE = BASE / "runtime" / "anu-execution.lock"

LOG_FILE = BASE / "logs" / "anu.log"
STATE_FILE = BASE / "runtime" / "anu-state.json"

INTERVAL_SECONDS = 60
SCHEDULER_MAX_TASKS = 1

running = True
recovery_done = False


def now():
    return datetime.now(timezone.utc).isoformat()


def write_log(message):
    line = f"{now()} [ANU] {message}\n"

    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line)


def save_state(data):
    temporary = STATE_FILE.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )

    temporary.replace(STATE_FILE)


def signal_handler(signum, frame):
    global running
    running = False


def _load_scheduler():
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_runtime",
        SCHEDULER,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load scheduler module: {SCHEDULER}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_recovery():
    spec = importlib.util.spec_from_file_location(
        "anu_recovery_v0_1_runtime",
        RECOVERY,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load recovery module: {RECOVERY}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_startup():
    for candidate in (
        STARTUP,
        _HERE / "anu-startup-recovery-v0.1.py",
    ):
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location(
            "anu_startup_recovery_v0_1_runtime",
            candidate,
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return None


def run_startup_recovery():
    global recovery_done

    # V0.6.8: prefer full startup sequence (mutation + approvals + wakes).
    startup = _load_startup()
    if startup is not None:
        try:
            summary = startup.run_startup_sequence(
                db_path=str(DB_FILE),
                lock_path=str(EXECUTION_LOCK_FILE),
                state_path=str(STATE_FILE),
                log_dir=str(BASE / "logs"),
            )
        except Exception as exc:
            write_log(
                "Startup recovery failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

        mutation = summary.get("mutation_recovery") or {}
        status = mutation.get("status", "OK")
        if status == "DEFERRED_ACTIVE_EXECUTION":
            write_log("Recovery deferred: execution in progress")
            return summary

        recovery_done = True
        wakes = summary.get("wakes") or {}
        inventory = summary.get("inventory") or {}
        write_log(
            f"Startup recovery {status}: "
            f"wakes_satisfied={len(wakes.get('satisfied', []))} "
            f"waiting={len(inventory.get('waiting_tasks', []))} "
            f"goals={len(inventory.get('active_goals', []))} "
            f"logs_rotated={len((summary.get('logs') or {}).get('rotated', []))}"
        )
        return summary

    try:
        result = _load_recovery().recover(
            db_path=str(DB_FILE),
            lock_path=str(EXECUTION_LOCK_FILE),
        )
    except Exception as exc:
        write_log(
            "Recovery failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return None

    status = result.get("status", "UNKNOWN")

    if status == "DEFERRED_ACTIVE_EXECUTION":
        write_log(
            "Recovery deferred: execution in progress"
        )
        return result

    recovery_done = True

    write_log(
        f"Recovery {status}: "
        f"transactions={len(result.get('transactions', []))} "
        f"tasks={len(result.get('tasks', []))}"
    )

    for item in result.get("transactions", []):
        write_log(
            f"Recovery transaction {item.get('transaction_id')}: "
            f"{item.get('status')} ({item.get('reason')})"
        )

    for item in result.get("tasks", []):
        retry = (
            f" retry_after={item['retry_after']}"
            if item.get("retry_after")
            else ""
        )
        write_log(
            f"Recovery task {item.get('task_id')}: "
            f"{item.get('previous_status')} -> {item.get('status')} "
            f"({item.get('reason')}){retry}"
        )

    return result


def scheduler_execution_allowed(guardian_data):
    if not isinstance(guardian_data, dict):
        return False

    return (
        str(
            guardian_data.get("status", "")
        ).strip().upper()
        == "NORMAL"
    )


def run_agent_scheduler_cycle(guardian_data):
    if not scheduler_execution_allowed(guardian_data):
        status = (
            guardian_data.get("status", "UNKNOWN")
            if isinstance(guardian_data, dict)
            else "UNKNOWN"
        )

        write_log(
            f"Scheduler skipped: resource status={status}"
        )

        return {
            "status": "SKIPPED_RESOURCE",
            "resource_status": status,
        }

    try:
        scheduler = _load_scheduler()

        result = scheduler.run_agent_scheduler_cycle_once(
            db_path=str(DB_FILE),
            max_tasks=SCHEDULER_MAX_TASKS,
            lock_path=str(SCHEDULER_LOCK_FILE),
        )

        if isinstance(result, list):
            write_log(
                f"Scheduler cycle completed: results={len(result)}"
            )
        elif isinstance(result, dict):
            write_log(
                f"Scheduler cycle completed: status="
                f"{result.get('status', 'UNKNOWN')}"
            )
        else:
            write_log(
                f"Scheduler cycle completed: result_type="
                f"{type(result).__name__}"
            )

        return result

    except Exception as exc:
        write_log(
            "Scheduler cycle failed: "
            f"{type(exc).__name__}: {exc}"
        )

        return None


def run_resource_guardian():
    try:
        result = subprocess.run(
            ["/usr/bin/python3", str(GUARDIAN)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        if result.returncode != 0:
            write_log(
                f"Resource Guardian failed: "
                f"exit={result.returncode}"
            )
            return None

        return json.loads(result.stdout)

    except Exception as exc:
        write_log(
            f"Resource Guardian exception: "
            f"{type(exc).__name__}: {exc}"
        )
        return None


def run_runtime_iteration():
    data = run_resource_guardian()

    if not data:
        write_log(
            "Health check unavailable."
        )
        return None

    status = data.get(
        "status",
        "UNKNOWN",
    )

    temperature = data.get(
        "temperature_c"
    )

    memory = data.get(
        "memory",
        {}
    )

    write_log(
        "Health check: "
        f"status={status} "
        f"temp={temperature}C "
        f"memory_available="
        f"{memory.get('available_mb')}MB"
    )

    save_state({
        "agent": "ANU",
        "status": status,
        "last_health_check": now(),
        "heartbeat_at": now(),
        "temperature_c": temperature,
        "memory": memory,
        "cpu_percent": data.get(
            "cpu_percent"
        ),
        "disk": data.get(
            "disk"
        ),
    })

    # A stop requested during the health check must not start new work.
    if not running:
        write_log(
            "Scheduler skipped: stop requested"
        )
        return {
            "status": "SKIPPED_STOPPING",
        }

    # Interrupted work is reconciled before any new work is scheduled.
    if not recovery_done:
        run_startup_recovery()

        if not recovery_done:
            return {
                "status": "SKIPPED_RECOVERY_PENDING",
            }

    return run_agent_scheduler_cycle(data)


def main():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    write_log("Runtime starting.")

    save_state({
        "agent": "ANU",
        "status": "starting",
        "started_at": now(),
    })

    while running:

        run_runtime_iteration()

        # Sleep in small chunks so SIGTERM is handled quickly.
        for _ in range(INTERVAL_SECONDS):

            if not running:
                break

            time.sleep(1)

    # V0.6.8: park durable state on SIGTERM before marking stopped.
    startup = _load_startup()
    if startup is not None:
        try:
            startup.park_for_shutdown(STATE_FILE, reason="SIGTERM")
        except Exception as exc:
            write_log(
                f"Park on shutdown failed: {type(exc).__name__}: {exc}"
            )

    save_state({
        "agent": "ANU",
        "status": "stopped",
        "stopped_at": now(),
        "park_reason": "SIGTERM",
    })

    write_log("Runtime stopped.")


if __name__ == "__main__":
    main()
