#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
GRAPH_MODULE = BASE / "bin" / "anu-task-graph-v0.1.py"
EXECUTION_SPEC_MODULE = (
    BASE / "bin" / "anu-task-execution-spec-v0.1.py"
)


def _load_graph():
    spec = importlib.util.spec_from_file_location(
        "anu_task_graph_v0_1_scheduler",
        GRAPH_MODULE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load Task Graph module: {GRAPH_MODULE}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_execution_spec():
    spec = importlib.util.spec_from_file_location(
        "anu_task_execution_spec_v0_1_scheduler",
        EXECUTION_SPEC_MODULE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load execution-spec module: "
            f"{EXECUTION_SPEC_MODULE}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_dependency_wake_plan(db, prerequisite_task_id):
    """
    Build a deterministic, read-only action plan for dependents.

    READY   -> WAKE
    WAITING -> WAIT
    BLOCKED -> BLOCK

    The task graph remains the source of dependency truth.
    This function does not mutate task state, attempts, or dependencies.
    """
    graph = _load_graph()

    evaluations = graph.evaluate_dependents(
        db,
        prerequisite_task_id,
    )

    actions = {
        "READY": "WAKE",
        "WAITING": "WAIT",
        "BLOCKED": "BLOCK",
    }

    plan = []

    for evaluation in evaluations:
        state = evaluation["state"]

        if state not in actions:
            raise ValueError(
                f"Unknown dependency state: {state!r}"
            )

        plan.append({
            "task_id": evaluation["task_id"],
            "action": actions[state],
            "current_status": evaluation["current_status"],
            "current_attempts": evaluation["current_attempts"],
            "state": state,
            "reason": evaluation["reason"],
        })

    return plan


def consume_dependency_wake_plan(plan, handlers):
    """
    Route a dependency wake plan to action handlers.

    This boundary is deliberately database-free and state-free.

    The complete plan is validated before any handler is called so an
    invalid action cannot cause partial execution.

    Handler inputs are deep-copied so handlers cannot mutate the
    caller-owned plan.
    """
    import copy

    if not isinstance(plan, list):
        raise TypeError("plan must be a list")

    if not isinstance(handlers, dict):
        raise TypeError("handlers must be a dictionary")

    supported_actions = {"WAKE", "WAIT", "BLOCK"}

    for item in plan:
        if not isinstance(item, dict):
            raise TypeError("each plan item must be a dictionary")

        action = item.get("action")

        if action not in supported_actions:
            raise ValueError(
                f"Unknown dependency wake action: {action!r}"
            )

        handler = handlers.get(action)

        if not callable(handler):
            raise ValueError(
                f"No callable handler registered for action: {action!r}"
            )

    results = []

    for item in plan:
        action = item["action"]
        handler = handlers[action]

        result = handler(copy.deepcopy(item))
        results.append(result)

    return results


def build_wake_execution_handler(start_task, execution_resolver):
    """
    Build a WAKE action handler around the existing task-engine start_task API.

    The adapter is deliberately database-free. It accepts only a READY/WAKE
    plan item, resolves the execution descriptor for that task, validates the
    descriptor, and delegates to start_task() without changing the plan item.
    """
    if not callable(start_task):
        raise TypeError("start_task must be callable")

    if not callable(execution_resolver):
        raise TypeError("execution_resolver must be callable")

    def handle(plan_item):
        if not isinstance(plan_item, dict):
            raise TypeError("plan_item must be a dictionary")

        if plan_item.get("action") != "WAKE":
            raise ValueError(
                f"WAKE handler received action={plan_item.get('action')!r}"
            )

        if plan_item.get("state") != "READY":
            raise ValueError(
                f"WAKE requires dependency state READY, "
                f"got={plan_item.get('state')!r}"
            )

        if "task_id" not in plan_item:
            raise ValueError("WAKE plan item missing task_id")

        task_id = plan_item["task_id"]
        execution = execution_resolver(task_id)

        if execution is None:
            raise ValueError(
                f"No execution descriptor for task_id={task_id}"
            )

        if not isinstance(execution, dict):
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
            if not execution.get(key)
        ]

        if missing:
            raise ValueError(
                "Execution descriptor missing required fields: "
                + ", ".join(missing)
            )

        return start_task(
            task_id,
            execution["project"],
            execution["changes_file"],
            execution["operation"],
        )

    return handle


def build_db_bound_wake_execution_handler(db, start_task):
    """Bind a database-backed execution descriptor resolver to WAKE."""
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    if not callable(start_task):
        raise TypeError("start_task must be callable")

    execution_spec = _load_execution_spec()

    fetch = getattr(
        execution_spec,
        "fetch_execution_descriptor",
        None,
    )

    if not callable(fetch):
        raise RuntimeError(
            "Execution-spec module must provide "
            "fetch_execution_descriptor(db, task_id)"
        )

    def execution_resolver(task_id):
        return fetch(db, task_id)

    return build_wake_execution_handler(
        start_task,
        execution_resolver,
    )


def resolve_execution_descriptor(db, task_id):
    """Resolve a task descriptor through the execution-spec owner.

    The scheduler does not issue execution-spec SQL directly.
    """
    execution_spec = _load_execution_spec()

    fetch = getattr(
        execution_spec,
        "fetch_execution_descriptor",
        None,
    )

    if not callable(fetch):
        raise RuntimeError(
            "Execution-spec module must provide "
            "fetch_execution_descriptor(db, task_id)"
        )

    return fetch(db, task_id)
