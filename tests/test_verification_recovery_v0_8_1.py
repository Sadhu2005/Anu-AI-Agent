#!/usr/bin/env python3
"""V0.8.1: structured recovery policy from verification decisions."""

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-verification-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("verify_v081", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_accept_none():
    m = load()
    out = m.recovery_policy_for_verification("ACCEPT")
    assert out["action"] == "none", out
    assert out["task_status"] == "completed", out
    assert out["retry_after_minutes"] is None
    assert out["retryable"] is False
    print("PASS ACCEPT -> none")


def test_retry_immediate():
    m = load()
    out = m.recovery_policy_for_verification(
        "RETRY",
        evidence={"mutation_status": "ROLLED_BACK", "questions": {"tests": False}},
        attempt=1,
        max_attempts=3,
    )
    assert out["action"] == "retry", out
    assert out["task_status"] == "failed", out
    assert out["retryable"] is True
    assert out["retry_after_minutes"] == 2, out
    assert out["failure_class"] == "code_defect", out
    print("PASS RETRY -> retry + 2m backoff")


def test_retry_wait_on_compile():
    m = load()
    out = m.recovery_policy_for_verification(
        "RETRY",
        evidence={"questions": {"compile": False}},
        attempt=2,
        max_attempts=3,
    )
    assert out["action"] == "wait", out
    assert out["task_status"] == "failed", out
    assert out["failure_class"] == "environment", out
    assert out["retry_after_minutes"] == 10, out
    print("PASS RETRY compile -> wait + 10m backoff")


def test_retry_escalate_at_max():
    m = load()
    out = m.recovery_policy_for_verification(
        "RETRY",
        attempt=3,
        max_attempts=3,
    )
    assert out["action"] == "escalate", out
    assert out["task_status"] == "blocked", out
    assert out["retryable"] is False
    assert out["retry_after_minutes"] is None
    print("PASS RETRY at max -> escalate")


def test_rollback_block():
    m = load()
    out = m.recovery_policy_for_verification(
        "ROLLBACK",
        mutation_status="ROLLBACK_VERIFICATION_FAILED",
        evidence={"questions": {"checkpoint": False}},
    )
    assert out["action"] == "block", out
    assert out["task_status"] == "blocked", out
    assert out["failure_class"] == "integrity", out
    print("PASS ROLLBACK -> block")


def test_rollback_escalate_environment():
    m = load()
    out = m.recovery_policy_for_verification(
        "ROLLBACK",
        mutation_status="ABORTED_BASELINE_FAILURE",
    )
    assert out["action"] == "escalate", out
    assert out["task_status"] == "blocked", out
    assert out["failure_class"] == "environment", out
    print("PASS ROLLBACK environment -> escalate")


def test_decision_to_task_outcome_enriched():
    m = load()
    out = m.decision_to_task_outcome(
        "RETRY",
        mutation_status="ROLLED_BACK",
        attempt=1,
        max_attempts=3,
    )
    assert out["recovery_action"] == "retry", out
    assert out["task_status"] == "failed", out
    assert "reason" in out
    print("PASS decision_to_task_outcome enriched")


def test_legacy_retry_outcome_shape():
    """Existing callers still get task_status / failure_class / retryable."""
    m = load()
    out = m.decision_to_task_outcome("RETRY")
    assert out["task_status"] == "failed"
    assert out["failure_class"] == "code_defect"
    assert out["retryable"] is True
    print("PASS legacy RETRY outcome fields")


def main():
    test_accept_none()
    test_retry_immediate()
    test_retry_wait_on_compile()
    test_retry_escalate_at_max()
    test_rollback_block()
    test_rollback_escalate_environment()
    test_decision_to_task_outcome_enriched()
    test_legacy_retry_outcome_shape()
    print("ALL V0.8.1 verification recovery tests PASS")


if __name__ == "__main__":
    main()
