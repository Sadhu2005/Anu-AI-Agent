#!/usr/bin/env python3

import subprocess
import sys
from pathlib import Path


TESTS = [
    Path("/srv/anu/tests/test_execution_context_v0_5_1.py"),
    Path("/srv/anu/tests/test_task_execution_context_e2e_v0_5_1.py"),
    Path("/srv/anu/tests/test_task_retry_v0_5_1.py"),
    Path("/srv/anu/tests/test_functional_failure_rollback_v0_5_1.py"),
    Path("/srv/anu/tests/test_retry_exhaustion_v0_5_2.py"),
    Path("/srv/anu/tests/test_checkpoint_corruption_v0_5_2.py"),
    Path("/srv/anu/tests/test_multi_file_mutation_v0_5_2.py"),
    Path("/srv/anu/tests/test_concurrent_contention_v0_5_2.py"),
]


def run_test(test_path):
    print("=" * 72)
    print(f"RUNNING: {test_path}")
    print("=" * 72)

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(test_path),
        ],
        capture_output=True,
        text=True,
        shell=False,
    )

    if result.stdout:
        print(result.stdout, end="")

    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")

    if result.returncode != 0:
        print(f"FAILED: {test_path}")
        return False

    print(f"PASSED: {test_path}")
    return True


def main():
    print("ANU V0.5.1 REGRESSION SUITE")
    print()

    failures = 0

    for test in TESTS:
        if not run_test(test):
            failures += 1

    print()
    print("=" * 72)

    if failures:
        print(f"REGRESSION_SUITE_FAILED count={failures}")
        return 1

    print("REGRESSION_SUITE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
