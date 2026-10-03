#!/usr/bin/env python3

import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml


BASE = Path("/srv/anu")
CONFIG_FILE = BASE / "config" / "anu-resources.yaml"
DB_FILE = BASE / "brain" / "anu-brain.db"


def load_config():
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def read_cpu_times():
    with open("/proc/stat", "r", encoding="utf-8") as f:
        line = f.readline().strip()

    parts = line.split()
    if not parts or parts[0] != "cpu":
        raise RuntimeError("Unable to read CPU statistics")

    values = list(map(int, parts[1:]))

    user = values[0]
    nice = values[1]
    system = values[2]
    idle = values[3]
    iowait = values[4] if len(values) > 4 else 0
    irq = values[5] if len(values) > 5 else 0
    softirq = values[6] if len(values) > 6 else 0
    steal = values[7] if len(values) > 7 else 0

    idle_total = idle + iowait
    total = user + nice + system + idle + iowait + irq + softirq + steal

    return total, idle_total


def cpu_percent(interval=0.5):
    total1, idle1 = read_cpu_times()

    time.sleep(interval)

    total2, idle2 = read_cpu_times()

    total_delta = total2 - total1
    idle_delta = idle2 - idle1

    if total_delta <= 0:
        return 0.0

    usage = (total_delta - idle_delta) / total_delta * 100.0
    return round(max(0.0, min(100.0, usage)), 2)


def read_memory():
    values = {}

    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0])

    total_kb = values.get("MemTotal", 0)
    available_kb = values.get("MemAvailable", values.get("MemFree", 0))
    used_kb = total_kb - available_kb

    return {
        "total_mb": round(total_kb / 1024, 2),
        "available_mb": round(available_kb / 1024, 2),
        "used_mb": round(used_kb / 1024, 2),
    }


def read_temperature():
    # Raspberry Pi preferred method.
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )

        output = result.stdout.strip()

        if output.startswith("temp="):
            value = output.replace("temp=", "").replace("'C", "")
            return round(float(value), 2)
    except Exception:
        pass

    # Generic Linux thermal fallback.
    thermal_root = Path("/sys/class/thermal")

    try:
        for zone in sorted(thermal_root.glob("thermal_zone*/temp")):
            raw = zone.read_text(encoding="utf-8").strip()

            if raw:
                return round(int(raw) / 1000.0, 2)
    except Exception:
        pass

    return None


def read_disk():
    usage = shutil.disk_usage("/")

    return {
        "total_gb": round(usage.total / (1024 ** 3), 2),
        "used_gb": round(usage.used / (1024 ** 3), 2),
        "free_gb": round(usage.free / (1024 ** 3), 2),
        "used_percent": round(
            usage.used / usage.total * 100,
            2
        ),
    }


def read_uptime():
    with open("/proc/uptime", "r", encoding="utf-8") as f:
        seconds = float(f.read().split()[0])

    return round(seconds, 2)


def determine_status(config, memory, temperature, disk):
    resources = config.get("resource_policy", {})

    temp_config = resources.get("temperature", {})
    memory_config = resources.get("memory", {})
    storage_config = resources.get("storage", {})

    warning_temp = float(
        temp_config.get("warning_celsius", 65)
    )

    pause_temp = float(
        temp_config.get("pause_celsius", 70)
    )

    emergency_memory = float(
        memory_config.get("emergency_free_mb", 1024)
    )

    minimum_disk = float(
        storage_config.get("minimum_free_gb", 20)
    )

    if temperature is not None and temperature >= pause_temp:
        return "PAUSE"

    if memory["available_mb"] < emergency_memory:
        return "PAUSE"

    if disk["free_gb"] < minimum_disk:
        return "PAUSE"

    if temperature is not None and temperature >= warning_temp:
        return "WARNING"

    return "NORMAL"


def save_to_database(data):
    connection = sqlite3.connect(DB_FILE)

    try:
        connection.execute(
            """
            INSERT INTO resource_usage
            (
                cpu_percent,
                memory_mb,
                temperature_c,
                disk_free_gb
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                data["cpu_percent"],
                data["memory"]["used_mb"],
                data["temperature_c"],
                data["disk"]["free_gb"],
            ),
        )

        connection.execute(
            """
            INSERT INTO agent_events
            (
                event_type,
                message,
                metadata
            )
            VALUES (?, ?, ?)
            """,
            (
                "RESOURCE_CHECK",
                f"Resource status: {data['status']}",
                json.dumps(data),
            ),
        )

        connection.commit()

    finally:
        connection.close()


def main():
    config = load_config()

    data = {
        "agent": "ANU",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hostname": os.uname().nodename,
        "architecture": os.uname().machine,
        "cpu_cores": os.cpu_count(),
        "cpu_percent": cpu_percent(),
        "memory": read_memory(),
        "temperature_c": read_temperature(),
        "disk": read_disk(),
        "load_average": list(os.getloadavg()),
        "uptime_seconds": read_uptime(),
    }

    data["status"] = determine_status(
        config,
        data["memory"],
        data["temperature_c"],
        data["disk"],
    )

    save_to_database(data)

    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
