#!/usr/bin/env python3

import importlib.util
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
GRAPH_MODULE = BASE / "bin" / "anu-task-graph-v0.1.py"
EXECUTION_SPEC_MODULE = (
    BASE / "bin" / "anu-task-execution-spec-v0.1.py"
)

LIFECYCLE_MODULE = BASE / "bin" / "anu-scheduler-runtime-v0.1.py"


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


def _load_lifecycle():
    spec = importlib.util.spec_from_file_location(
        "anu_scheduler_runtime_v0_1_production_scheduler",
        LIFECYCLE_MODULE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load scheduler lifecycle module: "
            f"{LIFECYCLE_MODULE}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_task_column(db, column):
    try:
        return any(row[1] == column for row in db.execute("PRAGMA table_info(tasks)"))
    except Exception:
        return False


def _retry_due(retry_after, now):
    """A pending task with a future (or unreadable) retry_after is not due."""
    if retry_after is None:
        return True

    try:
        due = datetime.fromisoformat(str(retry_after))
    except ValueError:
        return False

    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)

    return due <= now


def discover_ready_tasks(db, now=None):
    """Return pending tasks whose dependencies are currently READY.

    Discovery is strictly read-only. The Task Graph remains the source of
    dependency truth; this function never changes task status or attempts.
    A task whose retry_after backoff has not elapsed is skipped (V0.6.0).
    Results are ordered by priority descending, then task_id ascending.
    """
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    graph = _load_graph()
    now = now or datetime.now(timezone.utc)

    retry_after_column = (
        "retry_after"
        if _has_task_column(db, "retry_after")
        else "NULL AS retry_after"
    )

    rows = db.execute(
        f"""
        SELECT
            id AS task_id,
            status,
            priority,
            attempts,
            {retry_after_column}
        FROM tasks
        WHERE status = 'pending'
        ORDER BY priority DESC, id ASC
        """
    ).fetchall()

    ready = []

    for row in rows:
        if hasattr(row, "keys"):
            task_id = row["task_id"]
            status = row["status"]
            priority = row["priority"]
            attempts = row["attempts"]
            retry_after = row["retry_after"]
        else:
            task_id = row[0]
            status = row[1]
            priority = row[2]
            attempts = row[3]
            retry_after = row[4]

        if not _retry_due(retry_after, now):
            continue

        admission = graph.dependency_readiness(
            db,
            task_id,
        )

        if admission.get("state") != "READY":
            continue

        ready.append({
            "task_id": task_id,
            "status": status,
            "priority": priority,
            "attempts": attempts,
        })

    return ready


def run_scheduler_cycle_once(
    db_path,
    max_tasks,
    lock_path=None,
):
    """Run the legacy production scheduler lifecycle.

    This entrypoint intentionally preserves the V0.5.4 callback contract:
    scheduler lifecycle -> run_production_scheduler_cycle().
    """
    lifecycle = _load_lifecycle()

    return lifecycle.run_scheduler_cycle_once(
        db_path=db_path,
        max_tasks=max_tasks,
        cycle_fn=run_production_scheduler_cycle,
        lock_path=lock_path,
    )


def run_agent_scheduler_cycle_once(
    db_path,
    max_tasks,
    lock_path=None,
):
    """Run the agent-aware production scheduler lifecycle.

    This is the V0.5.8 production boundary for scheduler -> agent execution.
    The lifecycle still owns locking and the SQLite connection.
    """
    lifecycle = _load_lifecycle()

    return lifecycle.run_scheduler_cycle_once(
        db_path=db_path,
        max_tasks=max_tasks,
        cycle_fn=run_production_agent_scheduler_cycle,
        lock_path=lock_path,
    )


def run_production_scheduler_cycle(db, max_tasks):
    """Run one bounded production scheduling cycle.

    Discovery remains deterministic and read-only. Only the first
    ``max_tasks`` currently-ready candidates are revalidated and dispatched.
    The execution side effects remain behind the existing production WAKE
    binding and V0.5.3 Task Engine start_task boundary.
    """
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    if isinstance(max_tasks, bool) or not isinstance(max_tasks, int):
        raise TypeError("max_tasks must be a positive integer")

    if max_tasks <= 0:
        raise ValueError("max_tasks must be a positive integer")

    candidates = discover_ready_tasks(db)
    selected = candidates[:max_tasks]

    if not selected:
        return []

    plan = build_ready_task_wake_plan(db, selected)
    wake_handler = build_production_wake_execution_handler(db)

    return consume_dependency_wake_plan(
        plan,
        {
            "WAKE": wake_handler,
        },
    )


def run_production_agent_scheduler_cycle(db, max_tasks):
    """Run one bounded production cycle through the agent-aware WAKE path.

    This is the V0.5.8 agent-aware counterpart to
    run_production_scheduler_cycle(). The legacy function remains unchanged
    for compatibility with earlier scheduler contracts.
    """
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    if isinstance(max_tasks, bool) or not isinstance(max_tasks, int):
        raise TypeError("max_tasks must be a positive integer")

    if max_tasks <= 0:
        raise ValueError("max_tasks must be a positive integer")

    candidates = discover_ready_tasks(db)
    selected = candidates[:max_tasks]

    if not selected:
        return []

    plan = build_ready_task_wake_plan(db, selected)
    wake_handler = build_production_agent_wake_execution_handler(db)

    return consume_dependency_wake_plan(
        plan,
        {
            "WAKE": wake_handler,
        },
    )


def dispatch_production_ready_tasks(db):
    """Dispatch currently ready tasks through the production WAKE binding.

    This composes the tested scheduler discovery/plan/consumer path with the
    V0.5.3 Task Engine start_task boundary. The caller still owns the database
    connection; this function does not mutate scheduler state itself.
    """
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    wake_handler = build_production_wake_execution_handler(db)

    return dispatch_ready_task_wakes(
        db,
        {
            "WAKE": wake_handler,
        },
    )


def dispatch_ready_task_wakes(db, handlers):
    """Discover ready tasks, build a revalidated WAKE plan, and consume it.

    This is the scheduler orchestration boundary. It composes only the
    existing read-only discovery/planning layers and the existing wake-plan
    consumer. Execution remains behind the supplied handlers.
    """
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    if handlers is None:
        raise TypeError("handlers must be provided")

    candidates = discover_ready_tasks(db)
    plan = build_ready_task_wake_plan(db, candidates)

    return consume_dependency_wake_plan(plan, handlers)


def build_ready_task_wake_plan(db, candidates):
    """Convert ready scheduler candidates into a revalidated WAKE plan.

    The Task Graph remains the source of dependency truth. This function is
    strictly read-only: it never changes task status or attempts and never
    invokes the execution engine.
    """
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    if candidates is None:
        raise TypeError("candidates must be an iterable of candidate mappings")

    graph = _load_graph()
    plan = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise TypeError("each candidate must be a dict")

        required = (
            "task_id",
            "status",
            "priority",
            "attempts",
        )
        missing = [key for key in required if key not in candidate]
        if missing:
            raise KeyError(
                f"candidate missing required fields: {missing}"
            )

        if candidate["status"] != "pending":
            raise ValueError(
                f"candidate task {candidate['task_id']} is not pending"
            )

        admission = graph.dependency_readiness(db, candidate["task_id"])
        state = admission.get("state")

        if state != "READY":
            raise ValueError(
                f"candidate task {candidate['task_id']} is no longer READY: "
                f"{state!r}"
            )

        plan.append(
            {
                "task_id": candidate["task_id"],
                "action": "WAKE",
                "current_status": candidate["status"],
                "current_attempts": candidate["attempts"],
                "state": "READY",
            }
        )

    return plan


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


TASK_ENGINE_MODULE = (
    BASE / "bin" / "anu-task-engine-v0.5.3.py"
)

AGENT_EXECUTION_RUNTIME_MODULE = (
    BASE / "bin" / "anu-agent-task-execution-runtime-v0.1.py"
)


def _load_task_engine():
    """Load the frozen V0.5.3 Task Engine without executing a task."""
    spec = importlib.util.spec_from_file_location(
        "anu_task_engine_v0_5_3_scheduler",
        TASK_ENGINE_MODULE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load Task Engine module: {TASK_ENGINE_MODULE}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_agent_execution_runtime():
    spec = importlib.util.spec_from_file_location(
        "anu_agent_task_execution_runtime_v0_1_scheduler",
        AGENT_EXECUTION_RUNTIME_MODULE,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load agent execution runtime: "
            f"{AGENT_EXECUTION_RUNTIME_MODULE}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_production_agent_wake_execution_handler(db):
    """Bind production WAKE dispatch to the agent-aware execution runtime."""

    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    runtime = _load_agent_execution_runtime()

    handler_factory = getattr(
        runtime,
        "build_production_agent_execution_handler",
        None,
    )

    if not callable(handler_factory):
        raise RuntimeError(
            "Agent execution runtime must provide callable "
            "build_production_agent_execution_handler"
        )

    return handler_factory(db)


def build_production_wake_execution_handler(db):
    """Bind the frozen V0.5.3 start_task API to the DB-backed WAKE handler."""
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    engine = _load_task_engine()

    start_task = getattr(engine, "start_task", None)
    if not callable(start_task):
        raise RuntimeError(
            "Task Engine must provide callable start_task"
        )

    return build_db_bound_wake_execution_handler(
        db,
        start_task,
    )


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
