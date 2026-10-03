#!/usr/bin/env python3

import json
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/srv/anu")

GUARDIAN = BASE / "bin" / "anu-resource-guardian.py"
LOG_FILE = BASE / "logs" / "anu.log"
STATE_FILE = BASE / "runtime" / "anu-state.json"

INTERVAL_SECONDS = 60

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

        data = run_resource_guardian()

        if data:

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

        else:
            write_log(
                "Health check unavailable."
            )

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
