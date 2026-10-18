#!/usr/bin/env python3
"""Run the V0.7.2 portable regression pack (LLM planner + gateway)."""

import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

TESTS = [
    TESTS_DIR / "test_llm_planner_v0_7_2.py",
    TESTS_DIR / "test_model_gateway_config_v0_7_1.py",
    TESTS_DIR / "test_model_gateway_socket_v0_7_1.py",
]


def run_test(test_path: Path) -> bool:
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
    print("ANU V0.7.2 REGRESSION SUITE")
    failures = 0
    for test in TESTS:
        if not test.is_file() or not run_test(test):
            failures += 1
    print(f"files={len(TESTS)} failed={failures}")
    if failures:
        print("REGRESSION_SUITE_FAILED")
        return 1
    print("REGRESSION_SUITE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
