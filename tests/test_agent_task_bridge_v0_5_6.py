#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")

BRIDGE = (
    BASE / "bin" / "anu-agent-task-bridge-v0.1.py"
)

MODEL = (
    BASE / "bin" / "anu-agent-model-v0.1.py"
)


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


def test_task_id_validation():
    bridge = load_module(
        BRIDGE,
        "anu_agent_task_bridge_v0_1_test_id",
    )

    assert_raises(
        TypeError,
        lambda: bridge.build_agent_controller_for_task(
            "1",
            lambda _task_id: None,
            lambda _goal: None,
            lambda _action: None,
        ),
    )

    assert_raises(
        ValueError,
        lambda: bridge.build_agent_controller_for_task(
            0,
            lambda _task_id: None,
            lambda _goal: None,
            lambda _action: None,
        ),
    )

    print("PASS task_id validation")


def test_missing_task_rejected():
    bridge = load_module(
        BRIDGE,
        "anu_agent_task_bridge_v0_1_test_missing",
    )

    assert_raises(
        ValueError,
        lambda: bridge.build_agent_controller_for_task(
            200,
            lambda _task_id: None,
            lambda _goal: None,
            lambda _action: None,
        ),
    )

    print("PASS missing task rejected")


def test_fetcher_contract():
    bridge = load_module(
        BRIDGE,
        "anu_agent_task_bridge_v0_1_test_fetcher",
    )

    assert_raises(
        TypeError,
        lambda: bridge.build_agent_controller_for_task(
            201,
            lambda _task_id: [],
            lambda _goal: None,
            lambda _action: None,
        ),
    )

    print("PASS fetcher contract")


def test_build_controller_from_task_id():
    bridge = load_module(
        BRIDGE,
        "anu_agent_task_bridge_v0_1_test_controller",
    )

    model = load_module(
        MODEL,
        "anu_agent_model_v0_1_test_bridge_model",
    )

    task = {
        "id": 202,
        "goal_id": "goal-202",
        "description": "Bridge task",
        "priority": 5,
        "task_key": "TASK-202",
        "title": "Bridge",
        "parent_task_id": None,
        "status": "pending",
        "attempts": 0,
    }

    observed = []

    def fetch_task(task_id):
        observed.append(task_id)
        return task.copy()

    action = model.AgentAction(
        action_id="action-202",
        goal_id="goal-202",
        kind="TASK",
        description="Bridge action",
    )

    plan = model.AgentPlan(
        plan_id="plan-202",
        goal_id="goal-202",
        actions=(action,),
    )

    def planner_fn(goal):
        assert goal.goal_id == "goal-202"

        metadata = dict(goal.metadata)

        assert metadata["task_id"] == 202
        return plan

    def executor_fn(received_action):
        assert received_action.action_id == "action-202"

        return model.AgentResult(
            action_id="action-202",
            success=True,
            status="SUCCESS",
            message="bridge complete",
        )

    agent = bridge.build_agent_controller_for_task(
        task_id=202,
        task_fetcher=fetch_task,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
    )

    assert observed == [202]
    assert agent.goal.goal_id == "goal-202"

    report = agent.run()

    assert report["state"] == "COMPLETE"
    assert report["attempts"] == 1
    assert report["result_count"] == 1

    print("PASS task_id -> AgentController")


def test_run_task_id():
    bridge = load_module(
        BRIDGE,
        "anu_agent_task_bridge_v0_1_test_run",
    )

    model = load_module(
        MODEL,
        "anu_agent_model_v0_1_test_bridge_run_model",
    )

    task = {
        "id": 203,
        "goal_id": "goal-203",
        "description": "Run bridge",
        "priority": 3,
    }

    def fetch_task(task_id):
        assert task_id == 203
        return task

    action = model.AgentAction(
        action_id="action-203",
        goal_id="goal-203",
        kind="TASK",
        description="Run",
    )

    plan = model.AgentPlan(
        plan_id="plan-203",
        goal_id="goal-203",
        actions=(action,),
    )

    def planner_fn(_goal):
        return plan

    def executor_fn(received_action):
        return model.AgentResult(
            action_id=received_action.action_id,
            success=True,
            status="SUCCESS",
            message="done",
        )

    report = bridge.run_task_id_with_agent(
        task_id=203,
        task_fetcher=fetch_task,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
    )

    assert report["goal_id"] == "goal-203"
    assert report["state"] == "COMPLETE"

    print("PASS run_task_id_with_agent")


def test_no_database_dependency():
    bridge = load_module(
        BRIDGE,
        "anu_agent_task_bridge_v0_1_test_db",
    )

    calls = []

    def fetch_task(task_id):
        calls.append(task_id)

        return {
            "id": task_id,
            "goal_id": f"goal-{task_id}",
            "description": "No DB",
            "priority": 1,
        }

    agent_model = load_module(
        MODEL,
        "anu_agent_model_v0_1_test_bridge_db_model",
    )

    action = agent_model.AgentAction(
        action_id="action-204",
        goal_id="goal-204",
        kind="TASK",
        description="No DB action",
    )

    plan = agent_model.AgentPlan(
        plan_id="plan-204",
        goal_id="goal-204",
        actions=(action,),
    )

    def planner_fn(_goal):
        return plan

    def executor_fn(received_action):
        return agent_model.AgentResult(
            action_id=received_action.action_id,
            success=True,
            status="SUCCESS",
            message="done",
        )

    report = bridge.run_task_id_with_agent(
        204,
        fetch_task,
        planner_fn,
        executor_fn,
    )

    assert calls == [204]
    assert report["state"] == "COMPLETE"

    print("PASS bridge remains database-free")


if __name__ == "__main__":
    print("===== ANU V0.5.6 AGENT TASK BRIDGE REGRESSION =====")

    tests = [
        test_task_id_validation,
        test_missing_task_rejected,
        test_fetcher_contract,
        test_build_controller_from_task_id,
        test_run_task_id,
        test_no_database_dependency,
    ]

    for test in tests:
        test()

    print("AGENT_TASK_BRIDGE_TEST_COMPLETE")
