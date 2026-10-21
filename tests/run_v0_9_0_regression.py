#!/usr/bin/env python3
"""V0.9.0–V0.9.x regression pack runner."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "test_autonomy_v0_9_0.py",
    "test_assign_autonomy_v0_9_0.py",
    "test_auto_approval_v0_9_1.py",
    "test_llm_router_v0_9_2.py",
    "test_change_generator_v0_9_3.py",
    "test_git_workflow_v0_9_5.py",
    "test_dashboard_v0_9_6.py",
    "test_autonomy_integration_v0_9.py",
]


def main():
    failed = []
    for name in TESTS:
        path = ROOT / "tests" / name
        if not path.is_file():
            print(f"SKIP missing {name}")
            continue
        print(f"=== {name} ===")
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            failed.append(name)
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print("OK: all V0.9 packs passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
