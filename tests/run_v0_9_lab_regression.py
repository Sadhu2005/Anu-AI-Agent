#!/usr/bin/env python3
"""Portable V0.9.1–V0.9.15 lab regression pack (skip missing modules)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Explicit list: portable unittest modules for lab autonomy through V0.9.15.
# Missing files are skipped so partial checkouts still run what exists.
TESTS = [
    "test_auto_approval_v0_9_1.py",
    "test_llm_router_v0_9_2.py",
    "test_change_generator_v0_9_3.py",
    "test_git_workflow_v0_9_5.py",
    "test_dashboard_v0_9_6.py",
    "test_llm_failover_mocks_v0_9.py",
    "test_dashboard_v0_9_10.py",
    "test_test_matrix_v0_9_13.py",
    "test_lab_unattended_v0_9_15.py",
    "test_llm_router_usage_v1_1.py",
]


def main():
    failed = []
    ran = 0
    skipped = []
    for name in TESTS:
        path = ROOT / "tests" / name
        if not path.is_file():
            print(f"SKIP missing {name}")
            skipped.append(name)
            continue
        print(f"=== {name} ===")
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT),
        )
        ran += 1
        if proc.returncode != 0:
            failed.append(name)
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print(
        f"OK: V0.9 lab pack passed ({ran} modules"
        + (f", skipped {len(skipped)}" if skipped else "")
        + ")"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
