#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")
MODULE = BASE / "bin" / "anu-agent-model-v0.1.py"


def load_model():
    spec = importlib.util.spec_from_file_location(
        "anu_agent_model_v0_1_test",
        MODULE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load agent model: {MODULE}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_goal_contract():
    model = load_model()

    goal = model.AgentGoal(
        goal_id="goal-001",
        description="Inspect the ANU project",
        priority=5,
        metadata={
            "source": "user",
            "category": "engineering",
        },
    )

    assert goal.goal_id == "goal-001"
    assert goal.description == "Inspect the ANU project"
    assert goal.priority == 5

    assert goal.to_dict() == {
        "goal_id": "goal-001",
        "description": "Inspect the ANU project",
        "priority": 5,
        "metadata": {
            "category": "engineering",
            "source": "user",
        },
    }

    print("PASS goal contract")


def test_action_contract():
    model = load_model()

    action = model.AgentAction(
        action_id="action-001",
        goal_id="goal-001",
        kind="INSPECT",
        description="Inspect scheduler source",
        parameters={
            "path": "/srv/anu/bin",
            "recursive": True,
        },
    )

    assert action.action_id == "action-001"
    assert action.goal_id == "goal-001"
    assert action.kind == "INSPECT"

    assert action.to_dict() == {
        "action_id": "action-001",
        "goal_id": "goal-001",
        "kind": "INSPECT",
        "description": "Inspect scheduler source",
        "parameters": {
            "path": "/srv/anu/bin",
            "recursive": True,
        },
    }

    print("PASS action contract")


def test_plan_preserves_action_order():
    model = load_model()

    first = model.AgentAction(
        action_id="action-001",
        goal_id="goal-001",
        kind="INSPECT",
        description="Inspect files",
    )

    second = model.AgentAction(
        action_id="action-002",
        goal_id="goal-001",
        kind="TEST",
        description="Run tests",
    )

    plan = model.AgentPlan(
        plan_id="plan-001",
        goal_id="goal-001",
        actions=[second, first],
    )

    assert [action.action_id for action in plan.actions] == [
        "action-002",
        "action-001",
    ]

    assert [
        item["action_id"]
        for item in plan.to_dict()["actions"]
    ] == [
        "action-002",
        "action-001",
    ]

    print("PASS plan preserves action order")


def test_plan_rejects_wrong_goal():
    model = load_model()

    action = model.AgentAction(
        action_id="action-001",
        goal_id="goal-other",
        kind="TEST",
        description="Invalid goal binding",
    )

    try:
        model.AgentPlan(
            plan_id="plan-001",
            goal_id="goal-001",
            actions=[action],
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "plan accepted action belonging to another goal"
        )

    print("PASS plan rejects wrong goal")


def test_plan_rejects_duplicate_actions():
    model = load_model()

    action = model.AgentAction(
        action_id="action-001",
        goal_id="goal-001",
        kind="TEST",
        description="Duplicate action",
    )

    try:
        model.AgentPlan(
            plan_id="plan-001",
            goal_id="goal-001",
            actions=[action, action],
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "plan accepted duplicate action IDs"
        )

    print("PASS plan rejects duplicate actions")


def test_result_contract():
    model = load_model()

    result = model.AgentResult(
        action_id="action-001",
        success=True,
        status="SUCCESS",
        message="Inspection completed",
        data={
            "files": 12,
            "warnings": 0,
        },
    )

    assert result.success is True

    assert result.to_dict() == {
        "action_id": "action-001",
        "success": True,
        "status": "SUCCESS",
        "message": "Inspection completed",
        "data": {
            "files": 12,
            "warnings": 0,
        },
    }

    print("PASS result contract")


def test_invalid_inputs_rejected():
    model = load_model()

    invalid_goals = [
        (None, "description"),
        ("", "description"),
        ("goal", ""),
        ("goal", "   "),
    ]

    for goal_id, description in invalid_goals:
        try:
            model.AgentGoal(
                goal_id=goal_id,
                description=description,
            )
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(
                "invalid goal accepted"
            )

    try:
        model.AgentResult(
            action_id="action-001",
            success=1,
            status="SUCCESS",
            message="done",
        )
    except TypeError:
        pass
    else:
        raise AssertionError(
            "non-boolean success accepted"
        )

    print("PASS invalid model inputs rejected")


def test_models_are_immutable():
    model = load_model()

    goal = model.AgentGoal(
        goal_id="goal-001",
        description="Immutable goal",
    )

    action = model.AgentAction(
        action_id="action-001",
        goal_id="goal-001",
        kind="TEST",
        description="Immutable action",
    )

    plan = model.AgentPlan(
        plan_id="plan-001",
        goal_id="goal-001",
        actions=[action],
    )

    result = model.AgentResult(
        action_id="action-001",
        success=True,
        status="SUCCESS",
        message="Immutable result",
    )

    objects = [goal, action, plan, result]

    for obj in objects:
        field_name = next(iter(obj.__dataclass_fields__))

        try:
            setattr(obj, field_name, None)
        except Exception:
            pass
        else:
            raise AssertionError(
                f"{type(obj).__name__} is mutable"
            )

    print("PASS model objects immutable")


def test_nested_data_is_immutable():
    model = load_model()

    source = {
        "config": {
            "retries": 2,
            "enabled": True,
        },
        "items": ["a", "b"],
    }

    goal = model.AgentGoal(
        goal_id="goal-001",
        description="Nested data test",
        metadata=source,
    )

    source["config"]["retries"] = 999
    source["items"].append("c")

    assert goal.to_dict() == {
        "goal_id": "goal-001",
        "description": "Nested data test",
        "priority": 0,
        "metadata": {
            "config": {
                "retries": 2,
                "enabled": True,
            },
            "items": ["a", "b"],
        },
    }

    print("PASS nested model data is immutable")


if __name__ == "__main__":
    print("===== ANU V0.5.5 AGENT MODEL REGRESSION =====")

    test_goal_contract()
    test_action_contract()
    test_plan_preserves_action_order()
    test_plan_rejects_wrong_goal()
    test_plan_rejects_duplicate_actions()
    test_result_contract()
    test_invalid_inputs_rejected()
    test_models_are_immutable()
    test_nested_data_is_immutable()

    print("AGENT_MODEL_TEST_COMPLETE")
