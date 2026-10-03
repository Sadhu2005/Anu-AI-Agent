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
