#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")
ADAPTER = BASE / "bin" / "anu-agent-task-adapter-v0.1.py"
MODEL = BASE / "bin" / "anu-agent-model-v0.1.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(path)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_raises(exc_type, fn):
    try:
        fn()
    except exc_type:
        return
    raise AssertionError(
        f"expected {exc_type.__name__}"
    )


def test_task_to_goal():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_1_test",
    )

    task = {
        "id": 101,
        "goal_id": "goal-101",
        "description": "Run task through ANU",
        "priority": 7,
        "task_key": "TASK-101",
        "title": "Integration task",
        "parent_task_id": 22,
        "status": "pending",
        "attempts": 0,
    }

    goal = adapter.task_to_agent_goal(task)

    assert goal.goal_id == "goal-101"
    assert goal.description == "Run task through ANU"
    assert goal.priority == 7

    metadata = dict(goal.metadata)

    assert metadata["task_id"] == 101
    assert metadata["task_key"] == "TASK-101"
    assert metadata["parent_task_id"] == 22
    assert metadata["status"] == "pending"
    assert metadata["attempts"] == 0

    print("PASS task -> AgentGoal mapping")


def test_missing_goal_id_rejected():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_1_test_goal",
    )

    task = {
        "id": 102,
        "description": "Missing goal",
        "priority": 1,
    }

    assert_raises(
        ValueError,
        lambda: adapter.task_to_agent_goal(task),
    )

    print("PASS missing goal_id rejected")


def test_missing_description_rejected():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_1_test_description",
    )

    task = {
        "id": 103,
        "goal_id": "goal-103",
        "priority": 1,
    }

    assert_raises(
        ValueError,
        lambda: adapter.task_to_agent_goal(task),
    )

    print("PASS missing description rejected")


def test_invalid_task_type_rejected():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_1_test_type",
    )

    assert_raises(
        TypeError,
        lambda: adapter.task_to_agent_goal([]),
    )

    print("PASS invalid task type rejected")


def test_build_controller():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_1_test_controller",
    )

    model = load_module(
        MODEL,
        "anu_agent_model_v0_1_adapter_test_model",
    )

    task = {
        "id": 104,
        "goal_id": "goal-104",
        "description": "Controller adapter test",
        "priority": 3,
        "task_key": "TASK-104",
        "title": "Adapter",
        "status": "pending",
        "attempts": 0,
    }

    action = model.AgentAction(
        action_id="action-104",
        goal_id="goal-104",
        kind="TASK",
        description="Execute adapter action",
    )

    plan = model.AgentPlan(
        plan_id="plan-104",
        goal_id="goal-104",
        actions=(action,),
    )

    def planner_fn(goal):
        assert goal.goal_id == "goal-104"
        return plan

    def executor_fn(received_action):
        assert received_action.action_id == "action-104"

        return model.AgentResult(
            action_id="action-104",
            success=True,
            status="SUCCESS",
            message="adapter execution complete",
        )

    agent = adapter.build_agent_controller(
        task,
        planner_fn,
        executor_fn,
    )

    assert agent.goal.goal_id == "goal-104"

    report = agent.run()

    assert report["state"] == "COMPLETE"
    assert report["terminal"] is True
    assert report["attempts"] == 1
    assert report["retry_count"] == 0

    print("PASS adapter builds AgentController")


def test_run_task_with_agent():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_1_test_run",
    )

    model = load_module(
        MODEL,
        "anu_agent_model_v0_1_adapter_test_run_model",
    )

    task = {
        "id": 105,
        "goal_id": "goal-105",
        "description": "Run integration adapter",
        "priority": 5,
    }

    action = model.AgentAction(
        action_id="action-105",
        goal_id="goal-105",
        kind="TASK",
        description="Execute integration task",
    )

    plan = model.AgentPlan(
        plan_id="plan-105",
        goal_id="goal-105",
        actions=(action,),
    )

    def planner_fn(_goal):
        return plan

    def executor_fn(received_action):
        return model.AgentResult(
            action_id=received_action.action_id,
            success=True,
            status="SUCCESS",
            message="run completed",
        )

    report = adapter.run_task_with_agent(
        task,
        planner_fn,
        executor_fn,
    )

    assert report["agent_id"] == "task-agent-goal-105"
    assert report["goal_id"] == "goal-105"
    assert report["state"] == "COMPLETE"
    assert report["result_count"] == 1

    print("PASS run_task_with_agent lifecycle")


def test_adapter_does_not_require_database():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_1_test_db",
    )

    task = {
        "id": 106,
        "goal_id": "goal-106",
        "description": "Database-free adapter",
        "priority": 2,
    }

    goal = adapter.task_to_agent_goal(task)

    assert goal.goal_id == "goal-106"

    print("PASS adapter remains database-free")


def test_goal_metadata_is_immutable():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_1_test_immutable",
    )

    task = {
        "id": 107,
        "goal_id": "goal-107",
        "description": "Immutable metadata test",
        "priority": 1,
        "task_key": "TASK-107",
    }

    goal = adapter.task_to_agent_goal(task)

    try:
        goal.metadata["task_id"] = 999
    except Exception:
        pass
    else:
        raise AssertionError(
            "AgentGoal metadata must remain immutable"
        )

    print("PASS adapter preserves metadata immutability")


if __name__ == "__main__":
    print("===== ANU V0.5.6 AGENT TASK ADAPTER REGRESSION =====")

    tests = [
        test_task_to_goal,
        test_missing_goal_id_rejected,
        test_missing_description_rejected,
        test_invalid_task_type_rejected,
        test_build_controller,
        test_run_task_with_agent,
        test_adapter_does_not_require_database,
        test_goal_metadata_is_immutable,
    ]

    for test in tests:
        test()

    print("AGENT_TASK_ADAPTER_TEST_COMPLETE")
