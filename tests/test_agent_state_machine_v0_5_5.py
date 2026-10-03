#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")
MODULE = BASE / "bin" / "anu-agent-runtime-v0.1.py"


def load_agent_runtime():
    spec = importlib.util.spec_from_file_location(
        "anu_agent_runtime_v0_1_state_test",
        MODULE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load agent runtime: {MODULE}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_initial_state():
    agent = load_agent_runtime()
    machine = agent.AgentStateMachine("agent-test-001")

    assert machine.state is agent.AgentState.NEW
    assert machine.is_terminal is False
    assert machine.history == ()

    print("PASS initial state is NEW")


def test_full_success_path():
    agent = load_agent_runtime()
    machine = agent.AgentStateMachine("agent-test-002")

    path = [
        agent.AgentState.READY,
        agent.AgentState.PLANNING,
        agent.AgentState.EXECUTING,
        agent.AgentState.OBSERVING,
        agent.AgentState.COMPLETE,
    ]

    for state in path:
        machine.transition(state)

    assert machine.state is agent.AgentState.COMPLETE
    assert machine.is_terminal is True
    assert len(machine.history) == 5

    print("PASS full success lifecycle")


def test_retry_path():
    agent = load_agent_runtime()
    machine = agent.AgentStateMachine("agent-test-003")

    for state in [
        agent.AgentState.READY,
        agent.AgentState.PLANNING,
        agent.AgentState.EXECUTING,
        agent.AgentState.OBSERVING,
        agent.AgentState.RETRY,
        agent.AgentState.EXECUTING,
        agent.AgentState.OBSERVING,
        agent.AgentState.COMPLETE,
    ]:
        machine.transition(state)

    assert machine.state is agent.AgentState.COMPLETE
    assert len(machine.history) == 8

    print("PASS retry lifecycle")


def test_failed_path_is_terminal():
    agent = load_agent_runtime()
    machine = agent.AgentStateMachine("agent-test-004")

    machine.transition(agent.AgentState.READY)
    machine.transition(
        agent.AgentState.FAILED,
        reason="planning precondition failed",
    )

    assert machine.state is agent.AgentState.FAILED
    assert machine.is_terminal is True
    assert machine.history[-1].reason == (
        "planning precondition failed"
    )

    try:
        machine.transition(agent.AgentState.READY)
    except ValueError:
        pass
    else:
        raise AssertionError(
            "FAILED state must reject further transitions"
        )

    print("PASS failed state is terminal")


def test_invalid_transition_rejected():
    agent = load_agent_runtime()
    machine = agent.AgentStateMachine("agent-test-005")

    try:
        machine.transition(agent.AgentState.EXECUTING)
    except ValueError:
        pass
    else:
        raise AssertionError(
            "NEW -> EXECUTING must be rejected"
        )

    assert machine.state is agent.AgentState.NEW
    assert machine.history == ()

    print("PASS invalid transition rejected")


def test_state_string_coercion():
    agent = load_agent_runtime()
    machine = agent.AgentStateMachine("agent-test-006")

    machine.transition("READY")
    machine.transition("PLANNING")

    assert machine.state is agent.AgentState.PLANNING
    assert machine.can_transition("EXECUTING")
    assert not machine.can_transition("COMPLETE")

    print("PASS state string coercion")


def test_identity_validation():
    agent = load_agent_runtime()

    invalid_values = [
        None,
        "",
        "   ",
        123,
        False,
    ]

    for value in invalid_values:
        try:
            agent.AgentStateMachine(value)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(
                f"invalid agent_id accepted: {value!r}"
            )

    print("PASS agent identity validation")


def test_snapshot_contract():
    agent = load_agent_runtime()
    machine = agent.AgentStateMachine("agent-test-007")

    machine.transition(agent.AgentState.READY)
    machine.transition(agent.AgentState.PLANNING)

    snapshot = machine.snapshot()

    assert snapshot == {
        "agent_id": "agent-test-007",
        "state": "PLANNING",
        "terminal": False,
        "history_length": 2,
    }

    print("PASS agent snapshot contract")


def test_transition_record_is_immutable():
    agent = load_agent_runtime()
    machine = agent.AgentStateMachine("agent-test-008")

    transition = machine.transition(
        agent.AgentState.READY,
        reason="task accepted",
    )

    assert transition.agent_id == "agent-test-008"
    assert transition.from_state is agent.AgentState.NEW
    assert transition.to_state is agent.AgentState.READY
    assert transition.reason == "task accepted"

    try:
        transition.to_state = agent.AgentState.PLANNING
    except Exception:
        pass
    else:
        raise AssertionError(
            "transition record must be immutable"
        )

    print("PASS transition record immutable")


if __name__ == "__main__":
    print("===== ANU V0.5.5 AGENT STATE MACHINE REGRESSION =====")

    test_initial_state()
    test_full_success_path()
    test_retry_path()
    test_failed_path_is_terminal()
    test_invalid_transition_rejected()
    test_state_string_coercion()
    test_identity_validation()
    test_snapshot_contract()
    test_transition_record_is_immutable()

    print("AGENT_STATE_MACHINE_TEST_COMPLETE")
