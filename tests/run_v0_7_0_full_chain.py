#!/usr/bin/env python3
"""Chain portable regressions V0.6.1 → V0.7.0 (integration gate)."""

import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

RUNNERS = [
    TESTS_DIR / "run_v0_6_1_regression.py",
    TESTS_DIR / "run_v0_6_2_regression.py",
    TESTS_DIR / "run_v0_6_3_regression.py",
    TESTS_DIR / "run_v0_6_4_regression.py",
    TESTS_DIR / "run_v0_6_5_regression.py",
    TESTS_DIR / "run_v0_6_6_regression.py",
    TESTS_DIR / "run_v0_6_7_regression.py",
    TESTS_DIR / "run_v0_6_8_regression.py",
    TESTS_DIR / "run_v0_6_9_regression.py",
    TESTS_DIR / "run_v0_7_0_regression.py",
]


def main():
    print("ANU V0.7.0 FULL CHAIN (V0.6.1 → V0.7.0)")
    failures = 0
    for runner in RUNNERS:
        print("=" * 72)
        print(f"CHAIN: {runner.name}")
        print("=" * 72)
        if not runner.is_file():
            print(f"MISSING: {runner.name}")
            failures += 1
            continue
        result = subprocess.run(
            [sys.executable, "-B", str(runner)],
            cwd=str(REPO),
        )
        if result.returncode != 0:
            print(f"FAILED: {runner.name}")
            failures += 1
        else:
            print(f"PASSED: {runner.name}")
    print(f"runners={len(RUNNERS)} failed={failures}")
    if failures:
        print("FULL_CHAIN_FAILED")
        return 1
    print("FULL_CHAIN_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
