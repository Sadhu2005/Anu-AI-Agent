#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")

BRIDGE_MODULE = (
    BASE / "bin" / "anu-agent-task-bridge-v0.1.py"
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


def _load_bridge():
    return _load_module(
        BRIDGE_MODULE,
        "anu_agent_task_bridge_v0_1_execution_adapter",
    )


def _load_model():
    return _load_module(
        MODEL_MODULE,
        "anu_agent_model_v0_1_execution_adapter",
    )


def _validate_task_id(task_id):
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        raise TypeError("task_id must be an integer")

    if task_id <= 0:
        raise ValueError("task_id must be positive")

    return task_id


def _require_callable(value, name):
    if not callable(value):
        raise TypeError(f"{name} must be callable")


def _require_descriptor(descriptor):
    if descriptor is None:
        raise ValueError(
            "execution descriptor is required"
        )

    if not isinstance(descriptor, dict):
        raise TypeError(
            "execution_resolver must return a dictionary or None"
        )

    required = (
        "project",
        "changes_file",
        "operation",
    )

    missing = [
        key
        for key in required
        if not descriptor.get(key)
    ]

    if missing:
        raise ValueError(
            "Execution descriptor missing required fields: "
            + ", ".join(missing)
        )

    return {
        "project": descriptor["project"],
        "changes_file": descriptor["changes_file"],
        "operation": descriptor["operation"],
    }


def _build_planner(task_id, execution_resolver):
    model = _load_model()

    def planner_fn(goal):
        metadata = dict(goal.metadata)

        if metadata.get("task_id") != task_id:
            raise ValueError(
                "AgentGoal metadata task_id mismatch"
            )

        descriptor = _require_descriptor(
            execution_resolver(task_id)
        )

        action = model.AgentAction(
            action_id=f"task-action-{task_id}",
            goal_id=goal.goal_id,
            kind="TASK_EXECUTION",
            description=goal.description,
            parameters={
                "task_id": task_id,
                **descriptor,
            },
        )

        return model.AgentPlan(
            plan_id=f"task-plan-{task_id}",
            goal_id=goal.goal_id,
            actions=(action,),
        )

    return planner_fn


def _build_executor(task_id, start_task):
    model = _load_model()

    def executor_fn(action):
        parameters = dict(action.parameters)

        if parameters.get("task_id") != task_id:
            raise ValueError(
                "AgentAction task_id mismatch"
            )

        required = (
            "project",
            "changes_file",
            "operation",
        )

        missing = [
            key
            for key in required
            if not parameters.get(key)
        ]

        if missing:
            raise ValueError(
                "AgentAction missing execution fields: "
                + ", ".join(missing)
            )

        result = start_task(
            task_id,
            parameters["project"],
            parameters["changes_file"],
            parameters["operation"],
        )

        if (
            not isinstance(result, tuple)
            or len(result) != 2
        ):
            raise TypeError(
                "start_task must return (payload, exit_code)"
            )

        payload, exit_code = result

        if not isinstance(exit_code, int):
            raise TypeError(
                "start_task exit_code must be an integer"
            )

        if isinstance(payload, dict):
            status = payload.get(
                "task_status",
                "SUCCESS" if exit_code == 0 else "FAILED",
            )

            message = (
                payload.get("reason")
                or payload.get("message")
                or (
                    "task execution completed"
                    if exit_code == 0
                    else "task execution failed"
                )
            )
        else:
            status = (
                "SUCCESS"
                if exit_code == 0
                else "FAILED"
            )

            message = str(payload)

        return model.AgentResult(
            action_id=action.action_id,
            success=(exit_code == 0),
            status=str(status),
            message=str(message),
            data={
                "task_id": task_id,
                "exit_code": exit_code,
                "payload": payload,
            },
        )

    return executor_fn


def build_agent_controller_for_execution(
    task_id,
    task_fetcher,
    execution_resolver,
    start_task,
    max_retries=0,
):
    """
    Build an AgentController around the existing Task Engine.

    Planning is deterministic: one AgentAction is created from the
    persisted execution descriptor.

    Execution delegates exclusively to start_task(). The Task Engine
    therefore remains authoritative for dependency admission, attempts,
    mutation, rollback, and task state.

    AgentController retries are intentionally disabled here because
    Task Engine retry semantics already own task retries.
    """
    task_id = _validate_task_id(task_id)

    _require_callable(
        task_fetcher,
        "task_fetcher",
    )

    _require_callable(
        execution_resolver,
        "execution_resolver",
    )

    _require_callable(
        start_task,
        "start_task",
    )

    if (
        isinstance(max_retries, bool)
        or not isinstance(max_retries, int)
    ):
        raise TypeError(
            "max_retries must be a non-negative integer"
        )

    if max_retries != 0:
        raise ValueError(
            "execution adapter requires max_retries=0; "
            "Task Engine owns retry semantics"
        )

    bridge = _load_bridge()

    planner_fn = _build_planner(
        task_id,
        execution_resolver,
    )

    executor_fn = _build_executor(
        task_id,
        start_task,
    )

    return bridge.build_agent_controller_for_task(
        task_id=task_id,
        task_fetcher=task_fetcher,
        planner_fn=planner_fn,
        executor_fn=executor_fn,
        max_retries=0,
    )


def run_task_with_agent_execution(
    task_id,
    task_fetcher,
    execution_resolver,
    start_task,
):
    controller = build_agent_controller_for_execution(
        task_id=task_id,
        task_fetcher=task_fetcher,
        execution_resolver=execution_resolver,
        start_task=start_task,
        max_retries=0,
    )

    return controller.run()
