#!/usr/bin/env python3
"""V0.6.2 step 1: capability registry over gateway + profiles."""

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-capability-registry-v0.1.py"
GATEWAY = REPO / "config" / "anu-command-gateway.yaml"
PROFILES = REPO / "config" / "anu-execution-profiles.yaml"


def load():
    spec = importlib.util.spec_from_file_location("cap_reg_v062", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_registry_lists_known_capabilities():
    reg_mod = load()
    registry = reg_mod.CapabilityRegistry.from_files(GATEWAY, PROFILES)
    names = registry.list_capabilities()

    assert "git_status" in names
    assert "git_diff" in names
    assert "python_test" in names
    assert "python_compile" in names
    assert "python.test" in names
    assert "git.status" in names
    # V0.8.5: research is registered but approval-gated (not hard-disabled).
    assert "research" in names
    assert "browser" not in names

    cap = registry.get("python.test")
    assert cap["command"] == "python_test"
    assert cap["resource_limit"]["profile"] == "python-sandbox"
    assert cap["permission"]["sandbox_required"] is True
    assert "CAPABILITY_REQUEST" in cap["audit_record"]["events"]

    print("PASS registry lists known capabilities and aliases")


def test_require_denies_unknown_and_disabled():
    reg_mod = load()
    registry = reg_mod.CapabilityRegistry.from_files(GATEWAY, PROFILES)

    try:
        registry.require("not.a.real.capability")
        raise AssertionError("unknown capability should deny")
    except PermissionError as exc:
        assert "unknown capability" in str(exc)

    try:
        registry.require("filesystem.write")
        raise AssertionError("deferred capability should deny as disabled")
    except PermissionError as exc:
        assert "disabled" in str(exc)

    # research is listed; browser / filesystem.write stay hard-disabled.
    assert registry.require("research")["command"] == "research"

    assert registry.get("nope") is None
    print("PASS require denies unknown and disabled")


def test_validate_invocation():
    reg_mod = load()
    registry = reg_mod.CapabilityRegistry.from_files(GATEWAY, PROFILES)

    ok = registry.validate_invocation(
        "git.status",
        {"project": "sandbox-test-project", "task_id": 12},
    )
    assert ok["ok"] is True
    assert ok["command"] == "git_status"
    assert ok["profile"] == "git-read"

    try:
        registry.validate_invocation("git.status", {})
        raise AssertionError("missing project should fail")
    except ValueError as exc:
        assert "missing arguments" in str(exc) or "project" in str(exc)

    try:
        registry.validate_invocation(
            "git.status",
            {"project": "x", "task_id": True},
        )
        raise AssertionError("bool task_id should fail")
    except TypeError:
        pass

    print("PASS validate_invocation")


if __name__ == "__main__":
    print("===== ANU V0.6.2 CAPABILITY REGISTRY REGRESSION =====")
    test_registry_lists_known_capabilities()
    test_require_denies_unknown_and_disabled()
    test_validate_invocation()
    print("CAPABILITY_REGISTRY_TEST_COMPLETE")
