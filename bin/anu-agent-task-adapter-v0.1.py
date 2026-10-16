#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")

CONTROLLER_MODULE = (
    BASE / "bin" / "anu-agent-controller-v0.1.py"
)

MODEL_MODULE = (
    BASE / "bin" / "anu-agent-model-v0.1.py"
)


def _load_module(path, module_name):
    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load module: {path}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_controller():
    return _load_module(
        CONTROLLER_MODULE,
        "anu_agent_controller_v0_1_adapter",
    )


def _load_model():
    return _load_module(
        MODEL_MODULE,
        "anu_agent_model_v0_1_adapter",
    )


def _require_task_mapping(task):
    if not isinstance(task, dict):
        raise TypeError("task must be a dictionary")

    return task


def agent_admission(task):
    """Decide whether a task can become an AgentGoal without raising.

    Returns ``{"state": "READY"|"BLOCKED", "reason": str, ...}``.
    A BLOCKED result is for scheduler admission: the production agent
    path must block that one task (``failure_class=contract``) and
    continue the cycle, not fail the whole tick (V0.6.1).
    """
    task = _require_task_mapping(task)

    if task.get("goal_id") is None:
        return {
            "state": "BLOCKED",
            "reason": "task cannot become AgentGoal: goal_id is missing",
            "failure_class": "contract",
        }

    description = task.get("description")
    if not isinstance(description, str) or not description.strip():
        return {
            "state": "BLOCKED",
            "reason": "task cannot become AgentGoal: description is missing",
            "failure_class": "contract",
        }

    priority = task.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int):
        return {
            "state": "BLOCKED",
            "reason": "task cannot become AgentGoal: priority is invalid",
            "failure_class": "contract",
        }

    return {
        "state": "READY",
        "reason": "task can become AgentGoal",
    }


def task_to_agent_goal(task):
    """
    Convert a Task Engine task record into an AgentGoal.

    Required task fields:
      - goal_id
      - description
      - priority

    Scheduler/Task Engine identity and context are preserved inside
    immutable AgentGoal.metadata by the model contract.
    """
    admission = agent_admission(task)
    if admission["state"] != "READY":
        raise ValueError(admission["reason"])

    task = _require_task_mapping(task)

    goal_id = task.get("goal_id")
    description = task.get("description")
    priority = task.get("priority")

    # SQLite-backed Task Engine records use INTEGER goal IDs,
    # while the AgentGoal model requires a string identity.
    if isinstance(goal_id, int) and not isinstance(goal_id, bool):
        goal_id = str(goal_id)

    metadata = {
        "task_id": task.get("id", task.get("task_id")),
        "task_key": task.get("task_key"),
        "title": task.get("title"),
        "parent_task_id": task.get("parent_task_id"),
        "status": task.get("status"),
        "attempts": task.get("attempts"),
    }

    model = _load_model()

    return model.AgentGoal(
        goal_id=goal_id,
        description=description,
        priority=priority,
        metadata=metadata,
    )


def build_agent_controller(
    task,
    planner_fn,
    executor_fn,
    max_retries=0,
):
    """
    Build an AgentController for a Task Engine task.

    This adapter owns translation only. Scheduler/database logic remains
    outside the AgentController.
    """
    controller = _load_controller()

    goal = task_to_agent_goal(task)

    return controller.AgentController(
        agent_id=f"task-agent-{goal.goal_id}",
        goal=goal,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
        max_retries=max_retries,
    )


def run_task_with_agent(
    task,
    planner_fn,
    executor_fn,
    max_retries=0,
):
    agent = build_agent_controller(
        task=task,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
        max_retries=max_retries,
    )

    return agent.run()


if __name__ == "__main__":
    raise SystemExit(
        "anu-agent-task-adapter-v0.1.py is a library module; "
        "use task_to_agent_goal(), build_agent_controller(), "
        "or run_task_with_agent()"
    )
