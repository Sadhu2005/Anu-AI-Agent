#!/usr/bin/env python3
"""V0.6.9 failure-injection pack (assembles existing negative tests)."""

import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

# Portable on all hosts.
PORTABLE = [
    TESTS_DIR / "test_hot_pi_waits_and_rewakes_v0_6_6.py",
    TESTS_DIR / "test_graceful_shutdown_v0_6_8.py",
    TESTS_DIR / "test_boundary_self_check_v0_6_9.py",
]

# Require Linux (fcntl / /srv paths); run on Pi / Linux CI only.
LINUX_ONLY = [
    TESTS_DIR / "test_checkpoint_corruption_v0_5_2.py",
    TESTS_DIR / "test_orphan_task_recovery_v0_6_0.py",
]


def run_test(test_path: Path) -> bool:
    if not test_path.is_file():
        print(f"SKIP missing: {test_path.name}")
        return True
    print("=" * 72)
    print(f"RUNNING: {test_path.name}")
    print("=" * 72)
    result = subprocess.run(
        [sys.executable, "-B", str(test_path)],
        capture_output=True,
        text=True,
        shell=False,
        cwd=str(REPO),
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        print(f"FAILED: {test_path.name}")
        return False
    print(f"PASSED: {test_path.name}")
    return True


def main():
    print("ANU V0.6.9 FAILURE INJECTION PACK")
    failures = 0
    for test in PORTABLE:
        if not run_test(test):
            failures += 1
    if sys.platform.startswith("linux"):
        for test in LINUX_ONLY:
            if not run_test(test):
                failures += 1
    else:
        for test in LINUX_ONLY:
            print(f"SKIP linux-only: {test.name}")
    total = len(PORTABLE) + (
        len(LINUX_ONLY) if sys.platform.startswith("linux") else 0
    )
    print(f"files={total} failed={failures}")
    if failures:
        print("FAILURE_INJECTION_FAILED")
        return 1
    print("FAILURE_INJECTION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())