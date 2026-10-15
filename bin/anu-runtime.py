#!/usr/bin/env python3

import importlib.util
import json
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/srv/anu")

GUARDIAN = BASE / "bin" / "anu-resource-guardian.py"
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"
DB_FILE = BASE / "brain" / "anu-brain.db"
SCHEDULER_LOCK_FILE = BASE / "runtime" / "anu-scheduler.lock"

LOG_FILE = BASE / "logs" / "anu.log"
STATE_FILE = BASE / "runtime" / "anu-state.json"

INTERVAL_SECONDS = 60
SCHEDULER_MAX_TASKS = 1

running = True


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

    save_state({
        "agent": "ANU",
        "status": "stopped",
        "stopped_at": now(),
    })

    write_log("Runtime stopped.")


if __name__ == "__main__":
    main()
