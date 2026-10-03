#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
GRAPH_MODULE = BASE / "bin" / "anu-task-graph-v0.1.py"


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
