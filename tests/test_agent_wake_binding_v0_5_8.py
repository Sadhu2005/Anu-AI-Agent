#!/usr/bin/env python3

import copy
import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"


def load_scheduler():
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_agent_wake_v0_5_8",
        SCHEDULER,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load scheduler: {SCHEDULER}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDB:
    def execute(self, *args, **kwargs):
        return None


def test_agent_wake_binding_delegates_to_runtime():
    scheduler = load_scheduler()
    db = FakeDB()

    observed = []

    class FakeRuntime:
        @staticmethod
        def build_production_agent_execution_handler(received_db):
            observed.append(received_db)

            def handle(plan_item):
                return {
                    "handler": "agent",
                    "task_id": plan_item["task_id"],
                }

            return handle

    original_loader = scheduler._load_agent_execution_runtime

    try:
        scheduler._load_agent_execution_runtime = lambda: FakeRuntime()

        handler = scheduler.build_production_agent_wake_execution_handler(db)

        assert callable(handler)
        assert observed == [db]

        result = handler(
            {
                "action": "WAKE",
                "state": "READY",
                "task_id": 1801,
            }
        )

        assert result == {
            "handler": "agent",
            "task_id": 1801,
        }

        print("PASS agent WAKE binding delegates to agent runtime")

    finally:
        scheduler._load_agent_execution_runtime = original_loader


def test_production_agent_scheduler_cycle_uses_agent_wake_handler():
    scheduler = load_scheduler()
    db = FakeDB()

    candidates = [
        {
            "task_id": 1802,
            "status": "pending",
            "priority": 10,
            "attempts": 0,
        }
    ]

    observed = {
        "selected": [],
        "wake": [],
        "results": [],
    }

    original_discover = scheduler.discover_ready_tasks
    original_build_plan = scheduler.build_ready_task_wake_plan
    original_build_handler = (
        scheduler.build_production_agent_wake_execution_handler
    )
    original_consume = scheduler.consume_dependency_wake_plan

    try:
        def fake_discover(_db):
            return copy.deepcopy(candidates)

        def fake_build_plan(_db, selected):
            observed["selected"].append(copy.deepcopy(selected))

            return [
                {
                    "task_id": selected[0]["task_id"],
                    "action": "WAKE",
                    "current_status": "pending",
                    "current_attempts": 0,
                    "state": "READY",
                }
            ]

        def fake_build_handler(received_db):
            assert received_db is db

            def handle(plan_item):
                observed["wake"].append(copy.deepcopy(plan_item))
                return {
                    "task_id": plan_item["task_id"],
                    "agent": True,
                    "state": "COMPLETE",
                }

            return handle

        def fake_consume(plan, handlers):
            assert "WAKE" in handlers

            results = []

            for item in plan:
                result = handlers[item["action"]](
                    copy.deepcopy(item)
                )
                results.append(result)

            return results

        scheduler.discover_ready_tasks = fake_discover
        scheduler.build_ready_task_wake_plan = fake_build_plan
        scheduler.build_production_agent_wake_execution_handler = (
            fake_build_handler
        )
        scheduler.consume_dependency_wake_plan = fake_consume

        results = scheduler.run_production_agent_scheduler_cycle(
            db,
            max_tasks=1,
        )

        assert observed["selected"] == [candidates]
        assert observed["wake"] == [
            {
                "task_id": 1802,
                "action": "WAKE",
                "current_status": "pending",
                "current_attempts": 0,
                "state": "READY",
            }
        ]

        assert results == [
            {
                "task_id": 1802,
                "agent": True,
                "state": "COMPLETE",
            }
        ]

        print("PASS scheduler cycle uses agent WAKE handler")

    finally:
        scheduler.discover_ready_tasks = original_discover
        scheduler.build_ready_task_wake_plan = original_build_plan
        scheduler.build_production_agent_wake_execution_handler = (
            original_build_handler
        )
        scheduler.consume_dependency_wake_plan = original_consume


def test_agent_scheduler_cycle_once_uses_agent_cycle():
    scheduler = load_scheduler()

    observed = {}

    class FakeLifecycle:
        @staticmethod
        def run_scheduler_cycle_once(
            db_path,
            max_tasks,
            cycle_fn,
            lock_path=None,
        ):
            observed["db_path"] = db_path
            observed["max_tasks"] = max_tasks
            observed["cycle_fn"] = cycle_fn
            observed["lock_path"] = lock_path

            return {
                "status": "FAKE_AGENT_LIFECYCLE",
                "max_tasks": max_tasks,
            }

    original_loader = scheduler._load_lifecycle

    try:
        scheduler._load_lifecycle = lambda: FakeLifecycle()

        result = scheduler.run_agent_scheduler_cycle_once(
            db_path="/tmp/anu-v0.5.8-brain.db",
            max_tasks=3,
            lock_path="/tmp/anu-v0.5.8.lock",
        )

        assert observed["db_path"] == "/tmp/anu-v0.5.8-brain.db"
        assert observed["max_tasks"] == 3
        assert observed["cycle_fn"] is scheduler.run_production_agent_scheduler_cycle
        assert observed["lock_path"] == "/tmp/anu-v0.5.8.lock"

        assert result == {
            "status": "FAKE_AGENT_LIFECYCLE",
            "max_tasks": 3,
        }

        print("PASS agent scheduler lifecycle uses agent cycle")

    finally:
        scheduler._load_lifecycle = original_loader


if __name__ == "__main__":
    print("===== ANU V0.5.8 AGENT WAKE BINDING REGRESSION =====")

    test_agent_wake_binding_delegates_to_runtime()
    test_production_agent_scheduler_cycle_uses_agent_wake_handler()
    test_agent_scheduler_cycle_once_uses_agent_cycle()

    print("V0.5.8_AGENT_WAKE_BINDING_TEST_COMPLETE")
