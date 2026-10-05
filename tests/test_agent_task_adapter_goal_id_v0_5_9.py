#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")
ADAPTER = BASE / "bin" / "anu-agent-task-adapter-v0.1.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)

    if spec is None or spec.loader is None:
        raise RuntimeError(path)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_integer_goal_id_normalized():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_5_9_goal_id",
    )

    task = {
        "id": 186,
        "goal_id": 1,
        "description": "V0.5.9 integer DB goal ID",
        "priority": 1,
        "task_key": "TASK-V0.5.9",
        "title": "Integer goal ID",
        "parent_task_id": None,
        "status": "pending",
        "attempts": 0,
    }

    goal = adapter.task_to_agent_goal(task)

    assert goal.goal_id == "1"
    assert goal.description == task["description"]
    assert goal.priority == 1

    print("PASS integer DB goal_id normalized to AgentGoal string")


def test_string_goal_id_preserved():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_5_9_string_goal_id",
    )

    task = {
        "id": 187,
        "goal_id": "goal-187",
        "description": "V0.5.9 string goal ID",
        "priority": 2,
        "task_key": "TASK-V0.5.9-STRING",
        "title": "String goal ID",
        "parent_task_id": None,
        "status": "pending",
        "attempts": 0,
    }

    goal = adapter.task_to_agent_goal(task)

    assert goal.goal_id == "goal-187"

    print("PASS string goal_id preserved")


def test_missing_goal_id_still_rejected():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_adapter_v0_5_9_missing_goal",
    )

    task = {
        "id": 188,
        "description": "Missing goal ID",
        "priority": 1,
    }

    try:
        adapter.task_to_agent_goal(task)
    except ValueError as exc:
        assert "goal_id is missing" in str(exc)
    else:
        raise AssertionError("missing goal_id was not rejected")

    print("PASS missing goal_id still rejected")


if __name__ == "__main__":
    print("===== ANU V0.5.9 GOAL-ID ADAPTER REGRESSION =====")
    test_integer_goal_id_normalized()
    test_string_goal_id_preserved()
    test_missing_goal_id_still_rejected()
    print("V0.5.9_GOAL_ID_ADAPTER_TEST_COMPLETE")
