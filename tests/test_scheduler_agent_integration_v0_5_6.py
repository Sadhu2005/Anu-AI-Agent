#!/usr/bin/env python3

import copy
import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")

SCHEDULER = (
    BASE / "bin" / "anu-task-scheduler-v0.1.py"
)

BRIDGE = (
    BASE / "bin" / "anu-agent-task-bridge-v0.1.py"
)

MODEL = (
    BASE / "bin" / "anu-agent-model-v0.1.py"
)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Unable to load module: {path}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDB:
    def execute(self, *args, **kwargs):
        return None


def test_production_cycle_symbol_contract():
    scheduler = load_module(
        SCHEDULER,
        "anu_task_scheduler_v0_5_6_agent_symbol",
    )

    assert hasattr(
        scheduler,
        "run_production_scheduler_cycle",
    )

    fn = scheduler.run_production_scheduler_cycle

    assert callable(fn)

    print("PASS scheduler production cycle contract")


def test_ready_candidate_reaches_agent_bridge():
    scheduler = load_module(
        SCHEDULER,
        "anu_task_scheduler_v0_5_6_agent_handoff",
    )

    bridge = load_module(
        BRIDGE,
        "anu_agent_task_bridge_v0_1_scheduler_test",
    )

    model = load_module(
        MODEL,
        "anu_agent_model_v0_1_scheduler_test",
    )

    task_id = 305

    ready_candidates = [
        {
            "task_id": task_id,
            "status": "READY",
            "priority": 10,
            "attempts": 0,
        },
        {
            "task_id": 306,
            "status": "READY",
            "priority": 5,
            "attempts": 0,
        },
    ]

    selected_observed = []
    wake_plan_observed = []
    fetch_observed = []
    planner_observed = []
    executor_observed = []

    original_discover = scheduler.discover_ready_tasks
    original_build_plan = scheduler.build_ready_task_wake_plan
    original_build_handler = (
        scheduler.build_production_wake_execution_handler
    )
    original_consume = scheduler.consume_dependency_wake_plan

    try:
        def fake_discover(db):
            assert db is not None
            return copy.deepcopy(ready_candidates)

        def fake_build_plan(db, candidates):
            assert db is not None

            selected_observed.extend(
                copy.deepcopy(candidates)
            )

            return [
                {
                    "action": "WAKE",
                    "state": "READY",
                    "task_id": item["task_id"],
                }
                for item in candidates
            ]

        task_record = {
            "id": task_id,
            "goal_id": "goal-305",
            "description": "Scheduler agent integration",
            "priority": 10,
            "task_key": "TASK-305",
            "title": "Scheduler Agent",
            "parent_task_id": None,
            "status": "pending",
            "attempts": 0,
        }

        action = model.AgentAction(
            action_id="action-305",
            goal_id="goal-305",
            kind="TASK",
            description="Integration action",
        )

        plan = model.AgentPlan(
            plan_id="plan-305",
            goal_id="goal-305",
            actions=(action,),
        )

        def task_fetcher(received_task_id):
            fetch_observed.append(received_task_id)
            assert received_task_id == task_id
            return copy.deepcopy(task_record)

        def planner_fn(goal):
            planner_observed.append(goal.goal_id)

            assert goal.goal_id == "goal-305"

            metadata = dict(goal.metadata)

            assert metadata["task_id"] == task_id
            assert metadata["task_key"] == "TASK-305"

            return plan

        def executor_fn(received_action):
            executor_observed.append(
                received_action.action_id
            )

            return model.AgentResult(
                action_id=received_action.action_id,
                success=True,
                status="SUCCESS",
                message="scheduler-agent bridge complete",
            )

        def fake_build_handler(db):
            assert db is not None

            def handle(plan_item):
                item = copy.deepcopy(plan_item)

                wake_plan_observed.append(
                    copy.deepcopy(item)
                )

                assert item["action"] == "WAKE"
                assert item["state"] == "READY"
                assert item["task_id"] == task_id

                return bridge.run_task_id_with_agent(
                    task_id=item["task_id"],
                    task_fetcher=task_fetcher,
                    planner_fn=planner_fn,
                    executor_fn=executor_fn,
                )

            return handle

        def fake_consume(plan_items, handlers):
            assert "WAKE" in handlers

            results = []

            for item in plan_items:
                results.append(
                    handlers[item["action"]](
                        copy.deepcopy(item)
                    )
                )

            return results

        scheduler.discover_ready_tasks = fake_discover
        scheduler.build_ready_task_wake_plan = (
            fake_build_plan
        )
        scheduler.build_production_wake_execution_handler = (
            fake_build_handler
        )
        scheduler.consume_dependency_wake_plan = (
            fake_consume
        )

        fake_db = FakeDB()

        results = scheduler.run_production_scheduler_cycle(
            fake_db,
            max_tasks=1,
        )

        assert len(selected_observed) == 1
        assert selected_observed[0]["task_id"] == task_id

        assert len(wake_plan_observed) == 1
        assert wake_plan_observed[0]["task_id"] == task_id

        assert fetch_observed == [task_id]
        assert planner_observed == ["goal-305"]
        assert executor_observed == ["action-305"]

        assert len(results) == 1
        assert results[0]["goal_id"] == "goal-305"
        assert results[0]["state"] == "COMPLETE"

        print("PASS READY candidate reaches AgentController")

    finally:
        scheduler.discover_ready_tasks = original_discover
        scheduler.build_ready_task_wake_plan = (
            original_build_plan
        )
        scheduler.build_production_wake_execution_handler = (
            original_build_handler
        )
        scheduler.consume_dependency_wake_plan = (
            original_consume
        )


def test_max_tasks_limits_agent_handoff():
    scheduler = load_module(
        SCHEDULER,
        "anu_task_scheduler_v0_5_6_agent_limit",
    )

    observed = []

    original_discover = scheduler.discover_ready_tasks
    original_build_plan = scheduler.build_ready_task_wake_plan
    original_build_handler = (
        scheduler.build_production_wake_execution_handler
    )
    original_consume = scheduler.consume_dependency_wake_plan

    try:
        candidates = [
            {
                "task_id": 401,
                "status": "READY",
                "priority": 10,
                "attempts": 0,
            },
            {
                "task_id": 402,
                "status": "READY",
                "priority": 9,
                "attempts": 0,
            },
        ]

        def fake_discover(_db):
            return copy.deepcopy(candidates)

        def fake_build_plan(_db, selected):
            observed.extend(
                item["task_id"]
                for item in selected
            )

            return [
                {
                    "action": "WAKE",
                    "state": "READY",
                    "task_id": item["task_id"],
                }
                for item in selected
            ]

        def fake_handler(_db):
            return lambda item: item["task_id"]

        def fake_consume(plan, handlers):
            return [
                handlers[item["action"]](item)
                for item in plan
            ]

        scheduler.discover_ready_tasks = fake_discover
        scheduler.build_ready_task_wake_plan = (
            fake_build_plan
        )
        scheduler.build_production_wake_execution_handler = (
            fake_handler
        )
        scheduler.consume_dependency_wake_plan = (
            fake_consume
        )

        results = scheduler.run_production_scheduler_cycle(
            FakeDB(),
            max_tasks=1,
        )

        assert observed == [401]
        assert results == [401]

        print("PASS max_tasks bounds agent handoff")

    finally:
        scheduler.discover_ready_tasks = original_discover
        scheduler.build_ready_task_wake_plan = (
            original_build_plan
        )
        scheduler.build_production_wake_execution_handler = (
            original_build_handler
        )
        scheduler.consume_dependency_wake_plan = (
            original_consume
        )


def test_scheduler_does_not_mutate_candidate():
    scheduler = load_module(
        SCHEDULER,
        "anu_task_scheduler_v0_5_6_agent_immutable",
    )

    candidate = {
        "task_id": 501,
        "status": "READY",
        "priority": 7,
        "attempts": 2,
    }

    original_candidate = copy.deepcopy(candidate)

    original_discover = scheduler.discover_ready_tasks
    original_build_plan = scheduler.build_ready_task_wake_plan
    original_build_handler = (
        scheduler.build_production_wake_execution_handler
    )
    original_consume = scheduler.consume_dependency_wake_plan

    try:
        def fake_discover(_db):
            return [candidate]

        def fake_build_plan(_db, selected):
            assert selected[0] == original_candidate

            return [
                {
                    "action": "WAKE",
                    "state": "READY",
                    "task_id": selected[0]["task_id"],
                }
            ]

        def fake_handler(_db):
            return lambda item: item["task_id"]

        def fake_consume(plan, handlers):
            return [
                handlers[item["action"]](item)
                for item in plan
            ]

        scheduler.discover_ready_tasks = fake_discover
        scheduler.build_ready_task_wake_plan = (
            fake_build_plan
        )
        scheduler.build_production_wake_execution_handler = (
            fake_handler
        )
        scheduler.consume_dependency_wake_plan = (
            fake_consume
        )

        scheduler.run_production_scheduler_cycle(
            FakeDB(),
            max_tasks=1,
        )

        assert candidate == original_candidate

        print("PASS scheduler preserves candidate data")

    finally:
        scheduler.discover_ready_tasks = original_discover
        scheduler.build_ready_task_wake_plan = (
            original_build_plan
        )
        scheduler.build_production_wake_execution_handler = (
            original_build_handler
        )
        scheduler.consume_dependency_wake_plan = (
            original_consume
        )


if __name__ == "__main__":
    print(
        "===== ANU V0.5.6 "
        "SCHEDULER -> AGENT INTEGRATION REGRESSION ====="
    )

    tests = [
        test_production_cycle_symbol_contract,
        test_ready_candidate_reaches_agent_bridge,
        test_max_tasks_limits_agent_handoff,
        test_scheduler_does_not_mutate_candidate,
    ]

    for test in tests:
        test()

    print(
        "SCHEDULER_AGENT_INTEGRATION_TEST_COMPLETE"
    )
