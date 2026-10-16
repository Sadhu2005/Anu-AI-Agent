#!/usr/bin/env python3

import copy
import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BASE = REPO

RUNTIME = (
    BASE / "bin" / "anu-agent-task-execution-runtime-v0.1.py"
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
        raise RuntimeError(path)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_raises(exc_type, fn):
    try:
        fn()
    except exc_type:
        return

    raise AssertionError(
        f"expected {exc_type.__name__}"
    )


class FakeDB:
    def execute(self, *args, **kwargs):
        return None


class AlwaysReadyTaskAdapter:
    """V0.6.1 agent admission stub for older runtime tests."""

    @staticmethod
    def agent_admission(_task):
        return {
            "state": "READY",
            "reason": "task can become AgentGoal",
        }


def test_runtime_build_contract():
    runtime = load_module(
        RUNTIME,
        "anu_agent_task_execution_runtime_v0_1_contract",
    )

    class FakeAdapter:
        pass

    class FakeEngine:
        @staticmethod
        def fetch_task(_task_id):
            return {}

        @staticmethod
        def start_task(*_args):
            return ({}, 0)

        @staticmethod
        def update_task(*_args, **_kwargs):
            return None

        @staticmethod
        def event(*_args, **_kwargs):
            return None

    class FakeExecutionSpec:
        @staticmethod
        def fetch_execution_descriptor(_db, _task_id):
            return {
                "project": "project",
                "changes_file": "changes.json",
                "operation": "apply",
            }

    original_adapter = runtime._load_adapter
    original_engine = runtime._load_task_engine
    original_spec = runtime._load_execution_spec
    original_task_adapter = runtime._load_task_adapter

    try:
        runtime._load_adapter = lambda: FakeAdapter()
        runtime._load_task_engine = lambda: FakeEngine()
        runtime._load_execution_spec = lambda: FakeExecutionSpec()
        runtime._load_task_adapter = lambda: AlwaysReadyTaskAdapter()

        adapter_calls = []

        def fake_run_task_with_agent_execution(**kwargs):
            adapter_calls.append(kwargs)
            return {
                "state": "COMPLETE",
            }

        runtime._load_adapter().run_task_with_agent_execution = (
            fake_run_task_with_agent_execution
        )

        handler = runtime.build_production_agent_execution_handler(
            FakeDB()
        )

        assert callable(handler)

        print("PASS production runtime build contract")

    finally:
        runtime._load_adapter = original_adapter
        runtime._load_task_engine = original_engine
        runtime._load_execution_spec = original_spec
        runtime._load_task_adapter = original_task_adapter


def test_successful_production_agent_execution():
    runtime = load_module(
        RUNTIME,
        "anu_agent_task_execution_runtime_v0_1_success",
    )

    model = load_module(
        MODEL,
        "anu_agent_model_v0_1_runtime_success",
    )

    class FakeDB:
        def execute(self, *args, **kwargs):
            return None

    task_id = 701

    task = {
        "id": task_id,
        "goal_id": "goal-701",
        "description": "Production runtime success",
        "priority": 10,
        "task_key": "TASK-701",
        "title": "Runtime",
        "parent_task_id": None,
        "status": "pending",
        "attempts": 0,
    }

    descriptor = {
        "project": "sandbox-test-project",
        "changes_file": "changes.json",
        "operation": "apply",
    }

    observations = {
        "fetch": [],
        "descriptor": [],
        "start": [],
    }

    class FakeEngine:
        @staticmethod
        def fetch_task(received_task_id):
            observations["fetch"].append(received_task_id)
            return copy.deepcopy(task)

        @staticmethod
        def start_task(
            received_task_id,
            project,
            changes_file,
            operation,
        ):
            observations["start"].append(
                (
                    received_task_id,
                    project,
                    changes_file,
                    operation,
                )
            )

            return (
                {
                    "ok": True,
                    "task_id": received_task_id,
                    "task_status": "completed",
                    "attempt": 1,
                },
                0,
            )

        @staticmethod
        def update_task(*_args, **_kwargs):
            return None

        @staticmethod
        def event(*_args, **_kwargs):
            return None

    class FakeExecutionSpec:
        @staticmethod
        def fetch_execution_descriptor(db, received_task_id):
            assert isinstance(db, FakeDB)
            observations["descriptor"].append(
                received_task_id
            )
            return descriptor.copy()

    original_engine = runtime._load_task_engine
    original_spec = runtime._load_execution_spec
    original_task_adapter = runtime._load_task_adapter

    try:
        runtime._load_task_engine = lambda: FakeEngine()
        runtime._load_execution_spec = lambda: FakeExecutionSpec()
        runtime._load_task_adapter = lambda: AlwaysReadyTaskAdapter()

        handler = runtime.build_production_agent_execution_handler(
            FakeDB()
        )

        report = handler(
            {
                "action": "WAKE",
                "state": "READY",
                "task_id": task_id,
            }
        )

        assert report["goal_id"] == "goal-701"
        assert report["state"] == "COMPLETE"
        assert report["attempts"] == 1

        # fetch_task runs for agent admission, then again inside the adapter.
        assert observations["fetch"] == [task_id, task_id]
        assert observations["descriptor"] == [task_id]

        assert observations["start"] == [
            (
                task_id,
                "sandbox-test-project",
                "changes.json",
                "apply",
            )
        ]

        print("PASS successful production agent execution")

    finally:
        runtime._load_task_engine = original_engine
        runtime._load_execution_spec = original_spec
        runtime._load_task_adapter = original_task_adapter


def test_task_engine_failure_reaches_agent_boundary():
    runtime = load_module(
        RUNTIME,
        "anu_agent_task_execution_runtime_v0_1_failure",
    )

    task = {
        "id": 702,
        "goal_id": "goal-702",
        "description": "Production runtime failure",
        "priority": 1,
    }

    descriptor = {
        "project": "project",
        "changes_file": "changes.json",
        "operation": "apply",
    }

    class FakeEngine:
        @staticmethod
        def fetch_task(_task_id):
            return task.copy()

        @staticmethod
        def start_task(*_args):
            return (
                {
                    "ok": False,
                    "task_id": 702,
                    "task_status": "failed",
                    "reason": "mutation failed",
                },
                1,
            )

        @staticmethod
        def update_task(*_args, **_kwargs):
            return None

        @staticmethod
        def event(*_args, **_kwargs):
            return None

    class FakeExecutionSpec:
        @staticmethod
        def fetch_execution_descriptor(_db, _task_id):
            return descriptor.copy()

    original_engine = runtime._load_task_engine
    original_spec = runtime._load_execution_spec
    original_task_adapter = runtime._load_task_adapter

    try:
        runtime._load_task_engine = lambda: FakeEngine()
        runtime._load_execution_spec = lambda: FakeExecutionSpec()
        runtime._load_task_adapter = lambda: AlwaysReadyTaskAdapter()

        handler = runtime.build_production_agent_execution_handler(
            FakeDB()
        )

        report = handler(
            {
                "action": "WAKE",
                "state": "READY",
                "task_id": 702,
            }
        )

        assert report["goal_id"] == "goal-702"
        assert report["state"] == "FAILED"
        assert report["attempts"] == 1

        print("PASS Task Engine failure reaches agent boundary")

    finally:
        runtime._load_task_engine = original_engine
        runtime._load_execution_spec = original_spec
        runtime._load_task_adapter = original_task_adapter


def test_waiting_task_engine_result_is_preserved():
    runtime = load_module(
        RUNTIME,
        "anu_agent_task_execution_runtime_v0_1_waiting",
    )

    task = {
        "id": 704,
        "goal_id": "goal-704",
        "description": "Waiting dependency",
        "priority": 1,
    }

    descriptor = {
        "project": "project",
        "changes_file": "changes.json",
        "operation": "apply",
    }

    class FakeEngine:
        @staticmethod
        def fetch_task(_task_id):
            return task.copy()

        @staticmethod
        def start_task(*_args):
            return (
                {
                    "ok": False,
                    "task_id": 704,
                    "task_status": "waiting",
                    "attempt": 0,
                    "retryable": False,
                    "reason": "dependency not ready",
                },
                2,
            )

        @staticmethod
        def update_task(*_args, **_kwargs):
            return None

        @staticmethod
        def event(*_args, **_kwargs):
            return None

    class FakeExecutionSpec:
        @staticmethod
        def fetch_execution_descriptor(_db, _task_id):
            return descriptor.copy()

    original_engine = runtime._load_task_engine
    original_spec = runtime._load_execution_spec
    original_task_adapter = runtime._load_task_adapter

    try:
        runtime._load_task_engine = lambda: FakeEngine()
        runtime._load_execution_spec = lambda: FakeExecutionSpec()
        runtime._load_task_adapter = lambda: AlwaysReadyTaskAdapter()

        handler = runtime.build_production_agent_execution_handler(
            FakeDB()
        )

        report = handler(
            {
                "action": "WAKE",
                "state": "READY",
                "task_id": 704,
            }
        )

        assert report["goal_id"] == "goal-704"

        results = report.get("results", [])
        assert len(results) == 1

        result = results[0]

        assert result["success"] is False
        assert result["data"]["task_id"] == 704
        assert result["data"]["exit_code"] == 2
        assert (
            result["data"]["payload"]["task_status"]
            == "waiting"
        )

        print(
            "PASS Task Engine WAITING status preserved in result"
        )

    finally:
        runtime._load_task_engine = original_engine
        runtime._load_execution_spec = original_spec
        runtime._load_task_adapter = original_task_adapter


def test_invalid_plan_rejected():
    runtime = load_module(
        RUNTIME,
        "anu_agent_task_execution_runtime_v0_1_invalid",
    )

    class FakeEngine:
        @staticmethod
        def fetch_task(_task_id):
            return {}

        @staticmethod
        def start_task(*_args):
            return ({}, 0)

        @staticmethod
        def update_task(*_args, **_kwargs):
            return None

        @staticmethod
        def event(*_args, **_kwargs):
            return None

    class FakeExecutionSpec:
        @staticmethod
        def fetch_execution_descriptor(_db, _task_id):
            return {
                "project": "project",
                "changes_file": "changes.json",
                "operation": "apply",
            }

    original_engine = runtime._load_task_engine
    original_spec = runtime._load_execution_spec
    original_task_adapter = runtime._load_task_adapter

    try:
        runtime._load_task_engine = lambda: FakeEngine()
        runtime._load_execution_spec = lambda: FakeExecutionSpec()
        runtime._load_task_adapter = lambda: AlwaysReadyTaskAdapter()

        handler = runtime.build_production_agent_execution_handler(
            FakeDB()
        )

        assert_raises(
            ValueError,
            lambda: handler(
                {
                    "action": "WAKE",
                    "state": "WAITING",
                    "task_id": 703,
                }
            ),
        )

        assert_raises(
            ValueError,
            lambda: handler(
                {
                    "action": "WAKE",
                    "state": "READY",
                    "task_id": 0,
                }
            ),
        )

        print("PASS invalid production plan rejected")

    finally:
        runtime._load_task_engine = original_engine
        runtime._load_execution_spec = original_spec
        runtime._load_task_adapter = original_task_adapter


def test_runtime_requires_db():
    runtime = load_module(
        RUNTIME,
        "anu_agent_task_execution_runtime_v0_1_db",
    )

    assert_raises(
        TypeError,
        lambda: runtime.build_production_agent_execution_handler(
            object()
        ),
    )

    print("PASS production runtime DB contract")


if __name__ == "__main__":
    print(
        "===== ANU V0.5.6 "
        "PRODUCTION AGENT EXECUTION RUNTIME REGRESSION ====="
    )

    tests = [
        test_runtime_build_contract,
        test_successful_production_agent_execution,
        test_task_engine_failure_reaches_agent_boundary,
        test_waiting_task_engine_result_is_preserved,
        test_invalid_plan_rejected,
        test_runtime_requires_db,
    ]

    for test in tests:
        test()

    print(
        "PRODUCTION_AGENT_EXECUTION_RUNTIME_TEST_COMPLETE"
    )
