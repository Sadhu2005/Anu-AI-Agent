#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")

RUNTIME = BASE / "bin" / "anu-agent-runtime-v0.1.py"
CONTROLLER = BASE / "bin" / "anu-agent-controller-v0.1.py"
MODEL = BASE / "bin" / "anu-agent-model-v0.1.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_waiting_state_is_non_terminal():
    runtime = load_module(
        RUNTIME,
        "anu_agent_runtime_v0_1_waiting_v0_5_7",
    )

    assert runtime.AgentState.WAITING.value == "WAITING"

    machine = runtime.AgentStateMachine(
        "agent-waiting-state-001"
    )

    for state in [
        runtime.AgentState.READY,
        runtime.AgentState.PLANNING,
        runtime.AgentState.EXECUTING,
        runtime.AgentState.OBSERVING,
        runtime.AgentState.WAITING,
    ]:
        machine.transition(state)

    assert machine.state is runtime.AgentState.WAITING
    assert machine.is_terminal is False

    print("PASS WAITING state is non-terminal")


def test_controller_waiting_result_enters_waiting_state():
    controller = load_module(
        CONTROLLER,
        "anu_agent_controller_v0_1_waiting_v0_5_7",
    )
    model = load_module(
        MODEL,
        "anu_agent_model_v0_1_waiting_v0_5_7",
    )

    goal = model.AgentGoal(
        goal_id="goal-waiting-001",
        description="Waiting lifecycle contract",
        priority=1,
    )

    action = model.AgentAction(
        action_id="action-waiting-001",
        goal_id=goal.goal_id,
        kind="TEST",
        description="Waiting action",
    )

    plan = model.AgentPlan(
        plan_id="plan-waiting-001",
        goal_id=goal.goal_id,
        actions=(action,),
    )

    def planner_fn(received_goal):
        assert received_goal.goal_id == goal.goal_id
        return plan

    def executor_fn(received_action):
        assert received_action.action_id == action.action_id

        return model.AgentResult(
            action_id=received_action.action_id,
            success=False,
            status="waiting",
            message="dependency not ready",
            data={
                "task_id": 704,
                "exit_code": 2,
            },
        )

    agent = controller.AgentController(
        agent_id="agent-waiting-001",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
        max_retries=0,
    )

    report = agent.run()

    assert report["state"] == "WAITING"
    assert report["terminal"] is False
    assert report["attempts"] == 1
    assert report["retry_count"] == 0
    assert report["result_count"] == 1

    assert report["results"][0]["status"] == "waiting"
    assert report["results"][0]["success"] is False

    print("PASS controller WAITING result enters WAITING state")


if __name__ == "__main__":
    print(
        "===== ANU V0.5.7 WAITING LIFECYCLE REGRESSION ====="
    )

    test_waiting_state_is_non_terminal()
    test_controller_waiting_result_enters_waiting_state()

    print("V0.5.7_WAITING_LIFECYCLE_TEST_COMPLETE")
