#!/usr/bin/env python3
"""Runner for V0.9.15 lab unattended pack."""

from __future__ import annotations

import unittest
from pathlib import Path


def main():
    print("ANU V0.9.15 LAB UNATTENDED PACK")
    loader = unittest.TestLoader()
    suite = loader.discover(
        str(Path(__file__).resolve().parent),
        pattern="test_lab_unattended_v0_9_15.py",
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("LAB_UNATTENDED_PASS" if result.wasSuccessful() else "LAB_UNATTENDED_FAIL")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
