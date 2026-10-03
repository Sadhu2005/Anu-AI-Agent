#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")

ADAPTER_MODULE = (
    BASE / "bin" / "anu-agent-task-execution-adapter-v0.1.py"
)

TASK_ENGINE_MODULE = (
    BASE / "bin" / "anu-task-engine-v0.5.3.py"
)

EXECUTION_SPEC_MODULE = (
    BASE / "bin" / "anu-task-execution-spec-v0.1.py"
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
        "anu_agent_task_execution_adapter_v0_1_runtime",
    )


def _load_task_engine():
    return _load_module(
        TASK_ENGINE_MODULE,
        "anu_task_engine_v0_5_3_runtime",
    )


def _load_execution_spec():
    return _load_module(
        EXECUTION_SPEC_MODULE,
        "anu_task_execution_spec_v0_1_runtime",
    )


def _validate_db(db):
    if db is None or not hasattr(db, "execute"):
        raise TypeError(
            "db must provide an execute() method"
        )


def _validate_plan_item(plan_item):
    if not isinstance(plan_item, dict):
        raise TypeError(
            "plan_item must be a dictionary"
        )

    if plan_item.get("action") != "WAKE":
        raise ValueError(
            f"agent execution requires WAKE action, "
            f"got={plan_item.get('action')!r}"
        )

    if plan_item.get("state") != "READY":
        raise ValueError(
            f"agent execution requires READY state, "
            f"got={plan_item.get('state')!r}"
        )

    task_id = plan_item.get("task_id")

    if (
        isinstance(task_id, bool)
        or not isinstance(task_id, int)
        or task_id <= 0
    ):
        raise ValueError(
            "WAKE plan item must contain a positive integer task_id"
        )

    return task_id


def _require_callable(module, name):
    value = getattr(module, name, None)

    if not callable(value):
        raise RuntimeError(
            f"required callable missing: {name}"
        )

    return value


def build_production_agent_execution_handler(db):
    """
    Bind the frozen Task Engine and execution-spec contracts to the
    V0.5.6 Agent execution adapter.

    The scheduler database connection is used only for execution-spec
    resolution. Task Engine execution remains authoritative for task
    mutation, dependency admission, attempts, rollback, and task state.

    AgentController retries remain disabled.
    """
    _validate_db(db)

    adapter = _load_adapter()
    engine = _load_task_engine()
    execution_spec = _load_execution_spec()

    task_fetcher = _require_callable(
        engine,
        "fetch_task",
    )

    start_task = _require_callable(
        engine,
        "start_task",
    )

    fetch_execution_descriptor = _require_callable(
        execution_spec,
        "fetch_execution_descriptor",
    )

    def execution_resolver(task_id):
        return fetch_execution_descriptor(
            db,
            task_id,
        )

    def handle(plan_item):
        task_id = _validate_plan_item(plan_item)

        return adapter.run_task_with_agent_execution(
            task_id=task_id,
            task_fetcher=task_fetcher,
            execution_resolver=execution_resolver,
            start_task=start_task,
        )

    return handle
