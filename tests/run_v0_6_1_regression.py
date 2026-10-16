#!/usr/bin/env python3
"""Run the V0.6.1 Goal → Plan → Task Graph regression pack."""

import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

TESTS = [
    TESTS_DIR / "test_agent_admission_v0_6_1.py",
    TESTS_DIR / "test_goal_service_v0_6_1.py",
    TESTS_DIR / "test_goal_plan_service_v0_6_1.py",
    TESTS_DIR / "test_template_planner_v0_6_1.py",
    TESTS_DIR / "test_goal_aggregator_v0_6_1.py",
    TESTS_DIR / "test_goal_recovery_v0_6_1.py",
]


def run_test(test_path):
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
    print("ANU V0.6.1 REGRESSION SUITE")
    print(f"repo={REPO}")
    print()

    missing = [p for p in TESTS if not p.is_file()]
    if missing:
        for path in missing:
            print(f"MISSING: {path}")
        print("REGRESSION_SUITE_FAILED count=missing")
        return 1

    failures = 0
    for test in TESTS:
        if not run_test(test):
            failures += 1

    print()
    print("=" * 72)
    print(f"files={len(TESTS)} failed={failures}")
    if failures:
        print("REGRESSION_SUITE_FAILED")
        return 1

    print("REGRESSION_SUITE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
