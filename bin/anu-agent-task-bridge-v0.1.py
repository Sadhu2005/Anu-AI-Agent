#!/usr/bin/env python3

from pathlib import Path
import importlib.util


BASE = Path("/srv/anu")

ADAPTER_MODULE = (
    BASE / "bin" / "anu-agent-task-adapter-v0.1.py"
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


def _load_adapter():
    return _load_module(
        ADAPTER_MODULE,
        "anu_agent_task_adapter_v0_1_bridge",
    )


def _validate_task_id(task_id):
    if isinstance(task_id, bool):
        raise TypeError("task_id must be an integer")

    if not isinstance(task_id, int):
        raise TypeError("task_id must be an integer")

    if task_id <= 0:
        raise ValueError("task_id must be positive")

    return task_id


def build_agent_controller_for_task(
    task_id,
    task_fetcher,
    planner_fn,
    executor_fn,
    max_retries=0,
):
    """
    Resolve a Task Engine task by ID and build its AgentController.

    The bridge owns task lookup and translation orchestration only.
    It does not mutate the task or database.
    """
    task_id = _validate_task_id(task_id)

    if not callable(task_fetcher):
        raise TypeError("task_fetcher must be callable")

    task = task_fetcher(task_id)

    if task is None:
        raise ValueError(
            f"task not found: task_id={task_id}"
        )

    if not isinstance(task, dict):
        raise TypeError(
            "task_fetcher must return a dictionary or None"
        )

    adapter = _load_adapter()

    return adapter.build_agent_controller(
        task=task,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
        max_retries=max_retries,
    )


def run_task_id_with_agent(
    task_id,
    task_fetcher,
    planner_fn,
    executor_fn,
    max_retries=0,
):
    agent = build_agent_controller_for_task(
        task_id=task_id,
        task_fetcher=task_fetcher,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
        max_retries=max_retries,
    )

    return agent.run()


if __name__ == "__main__":
    raise SystemExit(
        "anu-agent-task-bridge-v0.1.py is a library module"
    )
