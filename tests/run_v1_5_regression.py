#!/usr/bin/env python3
"""V1.5 regression: handoff checks, then the V1.4 pack."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "test_hermes_verification_v1_5.py",
    "run_v1_4_regression.py",
]


def main():
    failed = []
    for name in TESTS:
        path = ROOT / "tests" / name
        if not path.is_file():
            print(f"FAIL missing {name}")
            return 1
        print(f"=== {name} ===")
        proc = subprocess.run([sys.executable, "-B", str(path)], cwd=str(ROOT))
        if proc.returncode != 0:
            failed.append(name)
            print(f"FAIL {name}")
            break
        print(f"PASS {name}")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print("OK: all V1.5 packs passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
