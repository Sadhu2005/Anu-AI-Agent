#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


BASE = Path("/srv/anu")

POLICY_FILE = (
    BASE / "config" / "anu-admission-policy.yaml"
)

WORKSPACE = (
    BASE / "workspace" / "projects"
)


def load_policy():
    with POLICY_FILE.open(
        "r",
        encoding="utf-8",
    ) as f:
        return yaml.safe_load(f) or {}


POLICY = load_policy()


def read_memory():
    values = {}

    with open(
        "/proc/meminfo",
        "r",
        encoding="utf-8",
    ) as f:

        for line in f:
            key, value = line.split(
                ":",
                1,
            )

            values[key] = int(
                value.strip().split()[0]
            )

    total_mb = (
        values["MemTotal"] / 1024
    )

    available_mb = (
        values.get(
            "MemAvailable",
            values.get(
                "MemFree",
                0,
            ),
        )
        / 1024
    )

    return {
        "total_mb": round(
            total_mb,
            2,
        ),
        "available_mb": round(
            available_mb,
            2,
        ),
        "used_mb": round(
            total_mb - available_mb,
            2,
        ),
    }


def read_temperature():
    """
    Read Raspberry Pi temperature.

    Try vcgencmd first using PATH discovery,
    then fall back to Linux thermal zones.
    """

    # Method 1: vcgencmd
    try:
        vcgencmd = shutil.which(
            "vcgencmd"
        )

        if vcgencmd:

            result = subprocess.run(
                [
                    vcgencmd,
                    "measure_temp",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )

            output = result.stdout.strip()

            if output.startswith("temp="):

                value = (
                    output
                    .replace(
                        "temp=",
                        "",
                    )
                    .replace(
                        "'C",
                        "",
                    )
                    .strip()
                )

                return float(value)

    except Exception:
        pass

    # Method 2: Linux thermal zones
    thermal_root = Path(
        "/sys/class/thermal"
    )

    try:

        for zone in sorted(
            thermal_root.glob(
                "thermal_zone*"
            )
        ):

            type_file = zone / "type"
            temp_file = zone / "temp"

            try:

                zone_type = (
                    type_file
                    .read_text(
                        encoding="utf-8"
                    )
                    .strip()
                    .lower()
                )

                raw = (
                    temp_file
                    .read_text(
                        encoding="utf-8"
                    )
                    .strip()
                )

                if not raw:
                    continue

                temperature = (
                    int(raw) / 1000.0
                )

                # Prefer CPU/GPU related Raspberry Pi sensors.
                preferred = (
                    "cpu",
                    "gpu",
                    "rp1",
                    "bcm",
                    "soc",
                )

                if any(
                    item in zone_type
                    for item in preferred
                ):
                    return temperature

                # Keep valid thermal reading as fallback.
                if (
                    temperature >= 0
                    and temperature < 120
                ):
                    fallback = temperature

            except Exception:
                continue

        try:
            return fallback
        except UnboundLocalError:
            pass

    except Exception:
        pass

    return None


def read_disk():
    usage = shutil.disk_usage("/")

    return {
        "total_gb": round(
            usage.total / 1024**3,
            2,
        ),
        "used_gb": round(
            usage.used / 1024**3,
            2,
        ),
        "free_gb": round(
            usage.free / 1024**3,
            2,
        ),
        "used_percent": round(
            usage.used / usage.total * 100,
            2,
        ),
    }


def workspace_size():
    total_bytes = 0

    if not WORKSPACE.exists():
        return 0.0

    for root, dirs, files in os.walk(
        WORKSPACE,
        followlinks=False,
    ):

        for filename in files:

            path = (
                Path(root)
                / filename
            )

            try:

                if path.is_symlink():
                    continue

                total_bytes += (
                    path.stat().st_size
                )

            except OSError:
                continue

    return total_bytes / 1024**3


def classify_file(size_mb):

    files = POLICY["files"]

    if size_mb <= files["normal_max_mb"]:
        return "ALLOW"

    if size_mb <= files["preflight_max_mb"]:
        return "PREFLIGHT"

    if size_mb <= files["approval_max_mb"]:
        return "APPROVAL"

    return "DENY"


def system_snapshot():

    return {
        "cpu_cores": os.cpu_count(),
        "memory": read_memory(),
        "disk": read_disk(),
        "temperature_c": read_temperature(),
        "workspace_gb": round(
            workspace_size(),
            3,
        ),
    }


def simple_deny(
    reason,
    snapshot,
    operation=None,
):

    return {
        "decision": "DENY",
        "operation": operation,
        "reason": reason,
        "snapshot": snapshot,
    }


def decide(
    operation,
    size_mb=0,
    ram_mb=0,
):

    policy = POLICY
    snapshot = system_snapshot()

    memory = snapshot["memory"]
    disk = snapshot["disk"]
    temp = snapshot["temperature_c"]

    safety = policy["host_safety"]
    budget = policy["anu_budget"]

    reasons = []
    warnings = []

    # ---------------------------------------
    # HARD POLICY DENIALS
    # ---------------------------------------

    if operation == "package":

        return simple_deny(
            "Host package installation is disabled by ANU policy.",
            snapshot,
            operation,
        )

    if operation == "docker":

        return simple_deny(
            "Docker operations are not enabled in ANU policy.",
            snapshot,
            operation,
        )

    if operation == "download":

        if not policy["downloads"]["enabled"]:

            return simple_deny(
                "Downloads are currently disabled by ANU policy.",
                snapshot,
                operation,
            )

    if operation == "model":

        model_policy = policy["models"]

        if not model_policy["enabled"]:

            return simple_deny(
                "Model installation is disabled by ANU policy.",
                snapshot,
                operation,
            )

    # ---------------------------------------
    # HOST RAM SAFETY
    # ---------------------------------------

    minimum_ram = float(
        safety["minimum_free_ram_mb"]
    )

    if (
        memory["available_mb"]
        < minimum_ram
    ):

        return {
            "decision": "PAUSE",
            "operation": operation,
            "reason": (
                "Available host RAM is below "
                "ANU safety reserve."
            ),
            "snapshot": snapshot,
        }

    # ---------------------------------------
    # REQUESTED RAM
    # ---------------------------------------

    if ram_mb > 0:

        maximum_ram = float(
            budget["maximum_ram_mb"]
        )

        if ram_mb > maximum_ram:

            reasons.append(
                "Requested RAM exceeds "
                "ANU RAM budget."
            )

        remaining = (
            memory["available_mb"]
            - ram_mb
        )

        if remaining < minimum_ram:

            reasons.append(
                "Requested workload would "
                "violate host RAM safety reserve."
            )

        if reasons:

            return {
                "decision": "DENY",
                "operation": operation,
                "requested_ram_mb": ram_mb,
                "reasons": reasons,
                "snapshot": snapshot,
            }

    # ---------------------------------------
    # HOST DISK SAFETY
    # ---------------------------------------

    minimum_disk = float(
        safety["minimum_free_disk_gb"]
    )

    if disk["free_gb"] < minimum_disk:

        return {
            "decision": "DENY",
            "operation": operation,
            "reason": (
                "Host free disk is below "
                "ANU safety reserve."
            ),
            "snapshot": snapshot,
        }

    # ---------------------------------------
    # REQUESTED STORAGE
    # ---------------------------------------

    if size_mb > 0:

        required_gb = (
            size_mb / 1024
        )

        remaining_disk = (
            disk["free_gb"]
            - required_gb
        )

        if remaining_disk < minimum_disk:

            return {
                "decision": "DENY",
                "operation": operation,
                "requested_size_mb": size_mb,
                "reason": (
                    "Requested storage would "
                    "violate host disk reserve."
                ),
                "snapshot": snapshot,
            }

    # ---------------------------------------
    # TEMPERATURE
    # ---------------------------------------

    warning_temp = float(
        safety["warning_temperature_c"]
    )

    pause_temp = float(
        safety["pause_temperature_c"]
    )

    if (
        temp is not None
        and temp >= pause_temp
    ):

        return {
            "decision": "PAUSE",
            "operation": operation,
            "reason": (
                "Raspberry Pi temperature is "
                "above ANU pause threshold."
            ),
            "snapshot": snapshot,
        }

    if (
        temp is not None
        and temp >= warning_temp
    ):

        warnings.append(
            "Temperature is elevated."
        )

    # ---------------------------------------
    # WORKSPACE STORAGE
    # ---------------------------------------

    workspace_limit = float(
        policy["storage"][
            "maximum_workspace_gb"
        ]
    )

    current_workspace = (
        snapshot["workspace_gb"]
    )

    if current_workspace >= workspace_limit:

        return {
            "decision": "DENY",
            "operation": operation,
            "reason": (
                "ANU workspace storage budget "
                "has been reached."
            ),
            "snapshot": snapshot,
        }

    # ---------------------------------------
    # FILE POLICY
    # ---------------------------------------

    if operation == "file":

        classification = classify_file(
            size_mb
        )

        if classification == "PREFLIGHT":

            return {
                "decision": "PREFLIGHT",
                "operation": operation,
                "requested_size_mb": size_mb,
                "warnings": [
                    "File is above normal size; "
                    "resource preflight is required."
                ],
                "snapshot": snapshot,
            }

        if classification == "APPROVAL":

            return {
                "decision": "APPROVAL",
                "operation": operation,
                "requested_size_mb": size_mb,
                "warnings": [
                    "Large file requires human approval."
                ],
                "snapshot": snapshot,
            }

        if classification == "DENY":

            return {
                "decision": "DENY",
                "operation": operation,
                "requested_size_mb": size_mb,
                "reason": (
                    "File exceeds ANU maximum "
                    "file size."
                ),
                "snapshot": snapshot,
            }

    # ---------------------------------------
    # MODEL SIZE POLICY
    # ---------------------------------------

    if operation == "model":

        model_policy = policy["models"]

        size_gb = size_mb / 1024

        if (
            size_gb
            > model_policy["absolute_max_gb"]
        ):

            return {
                "decision": "DENY",
                "operation": operation,
                "reason": (
                    "Model exceeds absolute "
                    "model size limit."
                ),
                "snapshot": snapshot,
            }

        if (
            size_gb
            > model_policy["approval_max_gb"]
        ):

            return {
                "decision": "DENY",
                "operation": operation,
                "reason": (
                    "Model exceeds maximum "
                    "approval size."
                ),
                "snapshot": snapshot,
            }

        if (
            size_gb
            > model_policy["normal_max_gb"]
        ):

            return {
                "decision": "APPROVAL",
                "operation": operation,
                "warnings": [
                    "Model exceeds normal "
                    "model budget."
                ],
                "snapshot": snapshot,
            }

    # ---------------------------------------
    # NORMAL ALLOW
    # ---------------------------------------

    return {
        "decision": "ALLOW",
        "operation": operation,
        "requested_size_mb": size_mb,
        "requested_ram_mb": ram_mb,
        "reasons": reasons,
        "warnings": warnings,
        "snapshot": snapshot,
    }


def main():

    parser = argparse.ArgumentParser(
        prog="anu-resource-governor"
    )

    parser.add_argument(
        "operation",
        choices=[
            "status",
            "task",
            "file",
            "model",
            "download",
            "package",
            "docker",
        ],
    )

    parser.add_argument(
        "--size-mb",
        type=float,
        default=0,
    )

    parser.add_argument(
        "--ram-mb",
        type=float,
        default=0,
    )

    args = parser.parse_args()

    if args.operation == "status":

        print(
            json.dumps(
                {
                    "agent": "ANU",
                    "policy": POLICY,
                    "snapshot": system_snapshot(),
                },
                indent=2,
            )
        )

        return

    response = decide(
        args.operation,
        args.size_mb,
        args.ram_mb,
    )

    print(
        json.dumps(
            response,
            indent=2,
        )
    )

    exit_codes = {
        "DENY": 2,
        "APPROVAL": 3,
        "PAUSE": 4,
        "PREFLIGHT": 5,
    }

    code = exit_codes.get(
        response["decision"],
        0,
    )

    sys.exit(code)


if __name__ == "__main__":
    main()
