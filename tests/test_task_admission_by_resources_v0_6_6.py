#!/usr/bin/env python3
"""V0.6.6: per-task RUN / WAIT / DENY from resource policy."""

import importlib.util
import os
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-resource-policy-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("rp_adm", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def policy():
    m = load()
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(REPO)
        return m, m.load_resource_policy()
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old


def test_run_when_healthy():
    m, p = policy()
    d = m.admit_task(
        p,
        host_status="NORMAL",
        memory_available_mb=3000,
        disk_free_gb=80,
        operation="python_test",
    )
    assert d["decision"] == "RUN", d
    print("PASS RUN when healthy")


def test_wait_heavy_under_low_ram():
    m, p = policy()
    d = m.admit_task(
        p,
        host_status="NORMAL",
        memory_available_mb=500,
        disk_free_gb=80,
        operation="python_test",
    )
    assert d["decision"] == "WAIT", d
    assert d["reason"] == "heavy_low_ram"
    assert d.get("retry_after")
    print("PASS WAIT heavy under low RAM")


def test_deny_low_disk():
    m, p = policy()
    d = m.admit_task(
        p,
        host_status="NORMAL",
        memory_available_mb=3000,
        disk_free_gb=10,
        operation="git_status",
    )
    assert d["decision"] == "DENY", d
    assert d["reason"] == "disk_low"
    print("PASS DENY low disk")


def test_wait_when_host_hot():
    m, p = policy()
    d = m.admit_task(
        p,
        host_status="WARNING",
        memory_available_mb=3000,
        disk_free_gb=80,
        operation="inspect",
    )
    assert d["decision"] == "WAIT", d
    assert "warning" in d["reason"]
    print("PASS WAIT when host WARNING")


def test_max_attempts_from_policy():
    m, p = policy()
    assert m.max_attempts(p) == 3
    print("PASS max_attempts from policy")


def main():
    print("===== ANU V0.6.6 TASK ADMISSION BY RESOURCES =====")
    test_run_when_healthy()
    test_wait_heavy_under_low_ram()
    test_deny_low_disk()
    test_wait_when_host_hot()
    test_max_attempts_from_policy()
    print("TASK_ADMISSION_BY_RESOURCES_TEST_COMPLETE")


if __name__ == "__main__":
    main()
