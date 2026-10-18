#!/usr/bin/env python3
"""V0.6.7: AUTONOMOUS / APPROVAL / DENY risk classification."""

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-approval-service-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("approval_cls", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_autonomous_normal_file():
    m = load()
    r = m.classify_action(action="python_test", size_mb=10)
    assert r["classification"] == "AUTONOMOUS", r
    print("PASS AUTONOMOUS normal file")


def test_approval_large_file():
    m = load()
    r = m.classify_action(action="large_file", size_mb=1500)
    assert r["classification"] == "APPROVAL", r
    print("PASS APPROVAL large file")


def test_deny_oversized_file():
    m = load()
    r = m.classify_action(action="upload", size_mb=3000)
    assert r["classification"] == "DENY", r
    print("PASS DENY oversized file")


def test_deny_dangerous():
    m = load()
    r = m.classify_action(action="docker")
    assert r["classification"] == "DENY", r
    r2 = m.classify_action(action="package")
    assert r2["classification"] == "DENY", r2
    print("PASS DENY dangerous operations")


def test_approval_production_and_major():
    m = load()
    r = m.classify_action(action="deploy", production_impacting=True)
    # deploy is in DENY_ACTIONS
    assert r["classification"] == "DENY", r
    r2 = m.classify_action(
        action="apply_change", production_impacting=True
    )
    assert r2["classification"] == "APPROVAL", r2
    r3 = m.classify_action(action="edit", major_mutation=True)
    assert r3["classification"] == "APPROVAL", r3
    print("PASS APPROVAL production / major mutation")


def test_deny_unknown():
    m = load()
    r = m.classify_action(action="", unknown=True)
    assert r["classification"] == "DENY", r
    print("PASS DENY unknown resource")


def main():
    print("===== ANU V0.6.7 RISK CLASSIFICATION =====")
    test_autonomous_normal_file()
    test_approval_large_file()
    test_deny_oversized_file()
    test_deny_dangerous()
    test_approval_production_and_major()
    test_deny_unknown()
    print("RISK_CLASSIFICATION_TEST_COMPLETE")


if __name__ == "__main__":
    main()
