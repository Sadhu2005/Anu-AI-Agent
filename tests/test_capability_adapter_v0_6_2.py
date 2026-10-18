#!/usr/bin/env python3
"""V0.6.2 follow-up: Safe Mutation ops go through capability adapter."""

import importlib.util
import json
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / "bin" / "anu-capability-adapter-v0.1.py"
GATEWAY_CFG = REPO / "config" / "anu-command-gateway.yaml"
PROFILE_CFG = REPO / "config" / "anu-execution-profiles.yaml"


def load_adapter():
    spec = importlib.util.spec_from_file_location("cap_adapter_t", ADAPTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_registry():
    path = REPO / "bin" / "anu-capability-registry-v0.1.py"
    spec = importlib.util.spec_from_file_location("cap_reg_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CapabilityRegistry.from_files(GATEWAY_CFG, PROFILE_CFG)


def test_operation_name_mapping():
    mod = load_adapter()
    assert mod.capability_name_for_operation("python_test") == "python.test"
    assert mod.capability_name_for_operation("git_status") == "git.status"
    print("PASS operation to capability name mapping")


def test_deny_unknown_operation():
    mod = load_adapter()
    registry = load_registry()
    run = mod.run_capability(
        "sandbox-test-project",
        "not_a_real_op",
        registry=registry,
        db=None,
    )
    assert run["admitted"] is False
    assert run["process_exit_code"] == 1
    assert run["result"]["capability_denied"] is True
    assert mod.capability_ok(run) is False
    print("PASS deny unknown operation via adapter")


def test_deny_deferred_filesystem_write():
    mod = load_adapter()
    registry = load_registry()
    run = mod.run_capability(
        "sandbox-test-project",
        "filesystem.write",
        registry=registry,
        db=None,
    )
    assert run["admitted"] is False
    assert run["result"].get("denial_class") == "disabled"
    print("PASS deny filesystem.write via adapter")


def test_admit_maps_to_invoke_shape():
    mod = load_adapter()
    registry = load_registry()

    fake = {
        "ok": True,
        "admitted": True,
        "decision": {
            "admitted": True,
            "command": "git_status",
            "profile": "git-read",
            "arguments": {"project": "sandbox-test-project", "task_id": None},
        },
        "gateway_exit_code": 0,
        "gateway_result": {"ok": True, "exit_code": 0, "output": "clean"},
        "stdout": json.dumps({"ok": True}),
        "stderr": "",
    }

    with mock.patch.object(registry, "invoke", return_value=fake):
        run = mod.run_capability(
            "sandbox-test-project",
            "git_status",
            registry=registry,
            db=None,
        )

    assert run["admitted"] is True
    assert run["process_exit_code"] == 0
    assert run["result"]["ok"] is True
    assert mod.capability_ok(run) is True
    print("PASS admitted invoke normalizes to run_gateway shape")


if __name__ == "__main__":
    print("===== ANU CAPABILITY ADAPTER REGRESSION =====")
    test_operation_name_mapping()
    test_deny_unknown_operation()
    test_deny_deferred_filesystem_write()
    test_admit_maps_to_invoke_shape()
    print("CAPABILITY_ADAPTER_TEST_COMPLETE")
