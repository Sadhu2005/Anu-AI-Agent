#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")
CONTROLLER = BASE / "bin" / "anu-agent-controller-v0.1.py"
MODEL = BASE / "bin" / "anu-agent-model-v0.1.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_controller():
    return load_module(
        CONTROLLER,
        "anu_agent_controller_v0_1_test",
    )


def load_model():
    return load_module(
        MODEL,
        "anu_agent_model_v0_1_controller_test",
    )


def make_goal(model):
    return model.AgentGoal(
        goal_id="goal-controller-001",
        description="Test controller lifecycle",
        priority=5,
    )


def make_plan(model, goal):
    action_one = model.AgentAction(
        action_id="action-001",
        goal_id=goal.goal_id,
        kind="TEST",
        description="First action",
    )

    action_two = model.AgentAction(
        action_id="action-002",
        goal_id=goal.goal_id,
        kind="TEST",
        description="Second action",
    )

    return model.AgentPlan(
        plan_id="plan-001",
        goal_id=goal.goal_id,
        actions=[action_one, action_two],
    )


def test_successful_controller_run():
    controller = load_controller()
    model = load_model()

    goal = make_goal(model)
    plan = make_plan(model, goal)

    planner_calls = []
    executor_calls = []

    def planner_fn(received_goal):
        planner_calls.append(received_goal.goal_id)
        return plan

    def executor_fn(action):
        executor_calls.append(action.action_id)

        return model.AgentResult(
            action_id=action.action_id,
            success=True,
            status="SUCCESS",
            message="action completed",
        )

    agent = controller.AgentController(
        agent_id="agent-controller-001",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
    )

    report = agent.run()

    assert report == {
        "agent_id": "agent-controller-001",
        "goal_id": "goal-controller-001",
        "state": "COMPLETE",
        "terminal": True,
        "attempts": 1,
        "retry_count": 0,
        "plan_id": "plan-001",
        "result_count": 2,
    }

    assert planner_calls == [
        "goal-controller-001",
    ]

    assert executor_calls == [
        "action-001",
        "action-002",
    ]

    assert agent.state.value == "COMPLETE"

    print("PASS successful controller lifecycle")


def test_controller_state_path():
    controller = load_controller()
    model = load_model()

    goal = make_goal(model)
    plan = make_plan(model, goal)

    def planner_fn(_goal):
        return plan

    def executor_fn(action):
        return model.AgentResult(
            action_id=action.action_id,
            success=True,
            status="SUCCESS",
            message="done",
        )

    agent = controller.AgentController(
        agent_id="agent-controller-002",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
    )

    agent.run()

    assert [
        item.from_state.value
        for item in agent.history
    ] == [
        "NEW",
        "READY",
        "PLANNING",
        "EXECUTING",
        "OBSERVING",
    ]

    assert [
        item.to_state.value
        for item in agent.history
    ] == [
        "READY",
        "PLANNING",
        "EXECUTING",
        "OBSERVING",
        "COMPLETE",
    ]

    print("PASS controller state path")


def test_failed_action_without_retry():
    controller = load_controller()
    model = load_model()

    goal = make_goal(model)
    plan = make_plan(model, goal)

    calls = []

    def planner_fn(_goal):
        return plan

    def executor_fn(action):
        calls.append(action.action_id)

        return model.AgentResult(
            action_id=action.action_id,
            success=False,
            status="FAILED",
            message="simulated failure",
        )

    agent = controller.AgentController(
        agent_id="agent-controller-003",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
        max_retries=0,
    )

    report = agent.run()

    assert report["state"] == "FAILED"
    assert report["attempts"] == 1
    assert report["retry_count"] == 0
    assert calls == ["action-001", "action-002"]

    print("PASS failed action without retry")


def test_failed_action_with_retry():
    controller = load_controller()
    model = load_model()

    goal = make_goal(model)
    plan = make_plan(model, goal)

    attempts = []

    def planner_fn(_goal):
        return plan

    def executor_fn(action):
        attempts.append(action.action_id)

        if len(attempts) <= 2:
            return model.AgentResult(
                action_id=action.action_id,
                success=False,
                status="FAILED",
                message="retryable failure",
            )

        return model.AgentResult(
            action_id=action.action_id,
            success=True,
            status="SUCCESS",
            message="recovered",
        )

    agent = controller.AgentController(
        agent_id="agent-controller-004",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
        max_retries=1,
    )

    report = agent.run()

    assert report["state"] == "COMPLETE"
    assert report["attempts"] == 2
    assert report["retry_count"] == 1

    assert [
        item.to_state.value
        for item in agent.history
    ] == [
        "READY",
        "PLANNING",
        "EXECUTING",
        "OBSERVING",
        "RETRY",
        "EXECUTING",
        "OBSERVING",
        "COMPLETE",
    ]

    print("PASS retry controller lifecycle")


def test_planner_goal_mismatch_rejected():
    controller = load_controller()
    model = load_model()

    goal = make_goal(model)

    wrong_goal = model.AgentGoal(
        goal_id="different-goal",
        description="Wrong goal",
    )

    wrong_plan = make_plan(model, wrong_goal)

    def planner_fn(_goal):
        return wrong_plan

    def executor_fn(_action):
        raise AssertionError(
            "executor must not run after invalid plan"
        )

    agent = controller.AgentController(
        agent_id="agent-controller-005",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
    )

    try:
        agent.run()
    except controller.AgentControllerError:
        pass
    else:
        raise AssertionError(
            "wrong-goal plan was accepted"
        )

    assert agent.state.value == "FAILED"
    assert agent.plan is None

    print("PASS planner goal mismatch rejected")


def test_invalid_executor_result_rejected():
    controller = load_controller()
    model = load_model()

    goal = make_goal(model)
    plan = make_plan(model, goal)

    def planner_fn(_goal):
        return plan

    def executor_fn(_action):
        return {
            "success": True,
        }

    agent = controller.AgentController(
        agent_id="agent-controller-006",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
    )

    try:
        agent.run()
    except controller.AgentControllerError:
        pass
    else:
        raise AssertionError(
            "invalid executor result was accepted"
        )

    assert agent.state.value == "FAILED"

    print("PASS invalid executor result rejected")


def test_result_action_mismatch_rejected():
    controller = load_controller()
    model = load_model()

    goal = make_goal(model)
    plan = make_plan(model, goal)

    def planner_fn(_goal):
        return plan

    def executor_fn(_action):
        return model.AgentResult(
            action_id="wrong-action",
            success=True,
            status="SUCCESS",
            message="wrong binding",
        )

    agent = controller.AgentController(
        agent_id="agent-controller-007",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
    )

    try:
        agent.run()
    except controller.AgentControllerError:
        pass
    else:
        raise AssertionError(
            "mismatched result was accepted"
        )

    assert agent.state.value == "FAILED"

    print("PASS result action mismatch rejected")


def test_empty_plan_completes():
    controller = load_controller()
    model = load_model()

    goal = make_goal(model)

    plan = model.AgentPlan(
        plan_id="empty-plan",
        goal_id=goal.goal_id,
        actions=[],
    )

    executor_calls = []

    def planner_fn(_goal):
        return plan

    def executor_fn(_action):
        executor_calls.append(True)
        raise AssertionError(
            "executor must not run for empty plan"
        )

    agent = controller.AgentController(
        agent_id="agent-controller-008",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
    )

    report = agent.run()

    assert report["state"] == "COMPLETE"
    assert report["attempts"] == 1
    assert report["result_count"] == 0
    assert executor_calls == []

    print("PASS empty plan completes")


def test_controller_cannot_run_twice():
    controller = load_controller()
    model = load_model()

    goal = make_goal(model)
    plan = make_plan(model, goal)

    def planner_fn(_goal):
        return plan

    def executor_fn(action):
        return model.AgentResult(
            action_id=action.action_id,
            success=True,
            status="SUCCESS",
            message="done",
        )

    agent = controller.AgentController(
        agent_id="agent-controller-009",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
    )

    agent.run()

    try:
        agent.run()
    except RuntimeError:
        pass
    else:
        raise AssertionError(
            "controller allowed second run"
        )

    print("PASS controller single-run contract")


def test_constructor_validation():
    controller = load_controller()
    model = load_model()

    goal = make_goal(model)

    def planner_fn(_goal):
        return model.AgentPlan(
            plan_id="plan",
            goal_id=goal.goal_id,
            actions=[],
        )

    def executor_fn(_action):
        raise AssertionError

    invalid_retries = [-1, True, "1"]

    for value in invalid_retries:
        try:
            controller.AgentController(
                agent_id="agent",
                goal=goal,
                planner_fn=planner_fn,
                executor_fn=executor_fn,
                max_retries=value,
            )
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(
                f"invalid max_retries accepted: {value!r}"
            )

    try:
        controller.AgentController(
            agent_id="agent",
            goal=object(),
            planner_fn=planner_fn,
            executor_fn=executor_fn,
        )
    except TypeError:
        pass
    else:
        raise AssertionError(
            "invalid goal accepted"
        )

    print("PASS controller constructor validation")


if __name__ == "__main__":
    print("===== ANU V0.5.5 AGENT CONTROLLER REGRESSION =====")

    test_successful_controller_run()
    test_controller_state_path()
    test_failed_action_without_retry()
    test_failed_action_with_retry()
    test_planner_goal_mismatch_rejected()
    test_invalid_executor_result_rejected()
    test_result_action_mismatch_rejected()
    test_empty_plan_completes()
    test_controller_cannot_run_twice()
    test_constructor_validation()

    print("AGENT_CONTROLLER_TEST_COMPLETE")
