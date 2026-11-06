#!/usr/bin/env python3
"""V1.4 regression: new Hermes contract layers plus the V1.3 and V0.9 packs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    "test_hermes_adapter_v1_4.py",
    "test_hermes_job_integration_v1_4.py",
    "test_hermes_job_functional_v1_4.py",
    "test_hermes_isolation_v1_3.py",
    "test_hermes_skillset_v1_3.py",
    "test_local_module_writer_v1_3.py",
    "test_ollama_provider_v1_2.py",
    "run_v0_9_0_regression.py",
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
    print("OK: all V1.4 packs passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
