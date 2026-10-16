#!/usr/bin/env python3
"""V0.6.1: a goal-less pending task must not fail the whole scheduler cycle."""

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / "bin" / "anu-agent-task-adapter-v0.1.py"
RUNTIME = REPO / "bin" / "anu-agent-task-execution-runtime-v0.1.py"
SCHEDULER = REPO / "bin" / "anu-task-scheduler-v0.1.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_admission_table():
    adapter = load(ADAPTER, "adapter_admission_table")

    assert adapter.agent_admission({
        "id": 1, "goal_id": 2, "description": "ok", "priority": 5,
    })["state"] == "READY"

    blocked = adapter.agent_admission({
        "id": 1, "goal_id": None, "description": "ok", "priority": 5,
    })
    assert blocked["state"] == "BLOCKED"
    assert blocked["failure_class"] == "contract"
    assert "goal_id is missing" in blocked["reason"]

    assert adapter.agent_admission({
        "id": 1, "goal_id": 2, "description": "  ", "priority": 5,
    })["state"] == "BLOCKED"

    assert adapter.agent_admission({
        "id": 1, "goal_id": 2, "description": "ok", "priority": True,
    })["state"] == "BLOCKED"

    try:
        adapter.task_to_agent_goal({
            "id": 1, "goal_id": None, "description": "ok", "priority": 5,
        })
    except ValueError as exc:
        assert "goal_id is missing" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    print("PASS agent_admission READY/BLOCKED table and task_to_agent_goal")


def test_handler_blocks_goal_less_task_without_raising():
    runtime = load(RUNTIME, "runtime_admission_block")
    adapter = load(ADAPTER, "adapter_admission_block")

    blocked = []
    events = []
    executed = []

    class FakeEngine:
        @staticmethod
        def fetch_task(task_id):
            if task_id == 1:
                return {
                    "id": 1,
                    "goal_id": None,
                    "description": "poison",
                    "priority": 5,
                    "status": "pending",
                    "attempts": 0,
                }
            return {
                "id": 2,
                "goal_id": 9,
                "description": "healthy",
                "priority": 5,
                "status": "pending",
                "attempts": 0,
            }

        @staticmethod
        def start_task(*_args, **_kwargs):
            raise AssertionError("start_task must not run for blocked admission")

        @staticmethod
        def update_task(task_id, status=None, failure_class=None, **_kwargs):
            blocked.append({
                "task_id": task_id,
                "status": status,
                "failure_class": failure_class,
            })

        @staticmethod
        def event(task_id, event_type, message, metadata=None):
            events.append({
                "task_id": task_id,
                "event_type": event_type,
                "message": message,
                "metadata": metadata or {},
            })

    class FakeExecutionSpec:
        @staticmethod
        def fetch_execution_descriptor(_db, _task_id):
            return {
                "project": "demo",
                "changes_file": "changes.json",
                "operation": "python_test",
            }

    class FakeAdapter:
        @staticmethod
        def run_task_with_agent_execution(**kwargs):
            executed.append(kwargs["task_id"])
            return {"state": "COMPLETE", "goal_id": "9"}

    class FakeDB:
        def execute(self, *_args, **_kwargs):
            return None

    original = (
        runtime._load_adapter,
        runtime._load_task_adapter,
        runtime._load_task_engine,
        runtime._load_execution_spec,
    )
    try:
        runtime._load_adapter = lambda: FakeAdapter()
        runtime._load_task_adapter = lambda: adapter
        runtime._load_task_engine = lambda: FakeEngine()
        runtime._load_execution_spec = lambda: FakeExecutionSpec()

        handler = runtime.build_production_agent_execution_handler(FakeDB())

        result = handler({
            "action": "WAKE",
            "state": "READY",
            "task_id": 1,
        })
        assert result["ok"] is False
        assert result["task_status"] == "blocked"
        assert result["failure_class"] == "contract"
        assert blocked == [{
            "task_id": 1,
            "status": "blocked",
            "failure_class": "contract",
        }]
        assert events[0]["event_type"] == "TASK_BLOCKED"
        assert executed == []

        ok = handler({
            "action": "WAKE",
            "state": "READY",
            "task_id": 2,
        })
        assert ok["state"] == "COMPLETE"
        assert executed == [2]
    finally:
        (
            runtime._load_adapter,
            runtime._load_task_adapter,
            runtime._load_task_engine,
            runtime._load_execution_spec,
        ) = original

    print("PASS handler blocks goal-less task and still wakes the next one")


def test_agent_cycle_continues_after_ineligible_candidate():
    """One poison candidate must not abort consume_dependency_wake_plan."""
    scheduler = load(SCHEDULER, "sched_admission_cycle")
    runtime = load(RUNTIME, "runtime_admission_cycle")
    adapter = load(ADAPTER, "adapter_admission_cycle")

    results_from_cycle = []
    blocked = []

    class FakeEngine:
        @staticmethod
        def fetch_task(task_id):
            rows = {
                1: {
                    "id": 1, "goal_id": None, "description": "bad",
                    "priority": 10, "status": "pending", "attempts": 0,
                },
                2: {
                    "id": 2, "goal_id": 3, "description": "good",
                    "priority": 5, "status": "pending", "attempts": 0,
                },
            }
            return rows[task_id]

        @staticmethod
        def start_task(*_a, **_k):
            raise AssertionError("not used")

        @staticmethod
        def update_task(task_id, status=None, failure_class=None, **_k):
            blocked.append((task_id, status, failure_class))

        @staticmethod
        def event(*_a, **_k):
            return None

    class FakeExecutionSpec:
        @staticmethod
        def fetch_execution_descriptor(_db, _task_id):
            return {
                "project": "demo",
                "changes_file": "c.json",
                "operation": "python_test",
            }

    class FakeAdapter:
        @staticmethod
        def run_task_with_agent_execution(**kwargs):
            return {"state": "COMPLETE", "task_id": kwargs["task_id"]}

    class FakeDB:
        def execute(self, *_a, **_k):
            return None

    original_runtime = (
        runtime._load_adapter,
        runtime._load_task_adapter,
        runtime._load_task_engine,
        runtime._load_execution_spec,
    )
    original_discover = scheduler.discover_ready_tasks
    original_plan = scheduler.build_ready_task_wake_plan
    original_handler = scheduler.build_production_agent_wake_execution_handler

    try:
        runtime._load_adapter = lambda: FakeAdapter()
        runtime._load_task_adapter = lambda: adapter
        runtime._load_task_engine = lambda: FakeEngine()
        runtime._load_execution_spec = lambda: FakeExecutionSpec()

        scheduler.discover_ready_tasks = lambda db, now=None: [
            {"task_id": 1, "status": "pending", "priority": 10, "attempts": 0},
            {"task_id": 2, "status": "pending", "priority": 5, "attempts": 0},
        ]
        scheduler.build_ready_task_wake_plan = lambda db, candidates: [
            {
                "task_id": c["task_id"],
                "action": "WAKE",
                "state": "READY",
                "current_status": c["status"],
                "current_attempts": c["attempts"],
            }
            for c in candidates
        ]
        scheduler.build_production_agent_wake_execution_handler = (
            lambda db: runtime.build_production_agent_execution_handler(db)
        )

        out = scheduler.run_production_agent_scheduler_cycle(FakeDB(), 2)
        results_from_cycle.extend(out)
    finally:
        (
            runtime._load_adapter,
            runtime._load_task_adapter,
            runtime._load_task_engine,
            runtime._load_execution_spec,
        ) = original_runtime
        scheduler.discover_ready_tasks = original_discover
        scheduler.build_ready_task_wake_plan = original_plan
        scheduler.build_production_agent_wake_execution_handler = original_handler

    assert len(results_from_cycle) == 2, results_from_cycle
    assert results_from_cycle[0]["task_status"] == "blocked"
    assert results_from_cycle[1]["state"] == "COMPLETE"
    assert blocked == [(1, "blocked", "contract")]

    print("PASS agent scheduler cycle continues after ineligible candidate")


if __name__ == "__main__":
    print("===== ANU V0.6.1 AGENT ADMISSION REGRESSION =====")
    test_agent_admission_table()
    test_handler_blocks_goal_less_task_without_raising()
    test_agent_cycle_continues_after_ineligible_candidate()
    print("AGENT_ADMISSION_TEST_COMPLETE")
