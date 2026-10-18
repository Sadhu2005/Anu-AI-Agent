#!/usr/bin/env python3
"""V0.6.6: temperature hysteresis — resume only below resume_celsius."""

import importlib.util
import os
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-resource-policy-v0.1.py"
GUARDIAN = REPO / "bin" / "anu-resource-guardian.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def policy():
    m = load(MOD, "rp_hot")
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(REPO)
        return m, m.load_resource_policy()
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old


def test_enter_warning_at_60():
    m, p = policy()
    status = m.determine_host_status(
        p,
        memory_available_mb=4000,
        temperature_c=60,
        disk_free_gb=80,
        previous_status="NORMAL",
    )
    assert status == "WARNING"
    print("PASS enter WARNING at 60C")


def test_hysteresis_holds_until_below_resume():
    m, p = policy()
    # After WARNING, 57C (above resume 55, below warning 60) stays WARNING.
    status = m.determine_host_status(
        p,
        memory_available_mb=4000,
        temperature_c=57,
        disk_free_gb=80,
        previous_status="WARNING",
    )
    assert status == "WARNING", status
    # Cool below resume → NORMAL.
    status2 = m.determine_host_status(
        p,
        memory_available_mb=4000,
        temperature_c=54,
        disk_free_gb=80,
        previous_status="WARNING",
    )
    assert status2 == "NORMAL", status2
    print("PASS hysteresis holds until below resume_celsius")


def test_guardian_determine_status_uses_previous():
    g = load(GUARDIAN, "guardian_hot")
    config = {
        "resource_policy": {
            "temperature": {
                "warning_celsius": 60,
                "pause_celsius": 65,
                "resume_celsius": 55,
            },
            "memory": {"emergency_free_mb": 1024},
            "storage": {"minimum_free_gb": 50},
        }
    }
    memory = {"available_mb": 4000}
    disk = {"free_gb": 80}
    # Without policy module path quirks: call with previous_status.
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(REPO)
        status = g.determine_status(
            config, memory, 57.0, disk, previous_status="WARNING"
        )
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    assert status == "WARNING", status
    print("PASS guardian determine_status hysteresis")


def test_state_file_roundtrip():
    m, _p = policy()
    root = Path(tempfile.mkdtemp())
    state_path = root / "anu-guardian-state.json"
    m.save_guardian_state("WARNING", path=state_path)
    loaded = m.load_guardian_state(path=state_path)
    assert loaded["status"] == "WARNING"
    print("PASS guardian state file roundtrip")


def main():
    print("===== ANU V0.6.6 HOT PI WAIT / REWAKE =====")
    test_enter_warning_at_60()
    test_hysteresis_holds_until_below_resume()
    test_guardian_determine_status_uses_previous()
    test_state_file_roundtrip()
    print("HOT_PI_WAIT_REWAKE_TEST_COMPLETE")


if __name__ == "__main__":
    main()
