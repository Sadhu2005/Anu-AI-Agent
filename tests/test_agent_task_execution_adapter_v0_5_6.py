#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")

ADAPTER = (
    BASE / "bin" / "anu-agent-task-execution-adapter-v0.1.py"
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


def test_successful_task_engine_delegation():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_execution_adapter_v0_1_success",
    )

    calls = []

    task = {
        "id": 601,
        "goal_id": "goal-601",
        "description": "Execute through Task Engine",
        "priority": 10,
        "task_key": "TASK-601",
        "title": "Execution",
        "parent_task_id": None,
        "status": "pending",
        "attempts": 0,
    }

    descriptor = {
        "project": "sandbox-test-project",
        "changes_file": "changes.json",
        "operation": "apply",
    }

    def task_fetcher(task_id):
        assert task_id == 601
        return task.copy()

    def execution_resolver(task_id):
        assert task_id == 601
        return descriptor.copy()

    def start_task(task_id, project, changes_file, operation):
        calls.append(
            (
                task_id,
                project,
                changes_file,
                operation,
            )
        )

        return (
            {
                "ok": True,
                "task_id": task_id,
                "task_status": "completed",
                "attempt": 1,
            },
            0,
        )

    report = adapter.run_task_with_agent_execution(
        task_id=601,
        task_fetcher=task_fetcher,
        execution_resolver=execution_resolver,
        start_task=start_task,
    )

    assert report["goal_id"] == "goal-601"
    assert report["state"] == "COMPLETE"
    assert report["attempts"] == 1

    assert calls == [
        (
            601,
            "sandbox-test-project",
            "changes.json",
            "apply",
        )
    ]

    print("PASS successful Task Engine delegation")


def test_failed_task_engine_result_propagates():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_execution_adapter_v0_1_failure",
    )

    task = {
        "id": 602,
        "goal_id": "goal-602",
        "description": "Failed execution",
        "priority": 1,
    }

    descriptor = {
        "project": "project",
        "changes_file": "changes.json",
        "operation": "apply",
    }

    def task_fetcher(_task_id):
        return task.copy()

    def execution_resolver(_task_id):
        return descriptor.copy()

    def start_task(_task_id, _project, _changes_file, _operation):
        return (
            {
                "ok": False,
                "task_id": 602,
                "task_status": "failed",
                "reason": "mutation failed",
            },
            1,
        )

    report = adapter.run_task_with_agent_execution(
        602,
        task_fetcher,
        execution_resolver,
        start_task,
    )

    assert report["goal_id"] == "goal-602"
    assert report["state"] == "FAILED"
    assert report["attempts"] == 1

    print("PASS Task Engine failure propagates")


def test_missing_execution_descriptor_rejected():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_execution_adapter_v0_1_missing",
    )

    task = {
        "id": 603,
        "goal_id": "goal-603",
        "description": "Missing descriptor",
        "priority": 1,
    }

    def task_fetcher(_task_id):
        return task.copy()

    def execution_resolver(_task_id):
        return None

    assert_raises(
        ValueError,
        lambda: adapter.run_task_with_agent_execution(
            603,
            task_fetcher,
            execution_resolver,
            lambda *_args: None,
        ),
    )

    print("PASS missing descriptor rejected")


def test_invalid_execution_descriptor_rejected():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_execution_adapter_v0_1_invalid",
    )

    task = {
        "id": 604,
        "goal_id": "goal-604",
        "description": "Invalid descriptor",
        "priority": 1,
    }

    def task_fetcher(_task_id):
        return task.copy()

    def execution_resolver(_task_id):
        return {
            "project": "project",
            "changes_file": "",
            "operation": "apply",
        }

    assert_raises(
        ValueError,
        lambda: adapter.run_task_with_agent_execution(
            604,
            task_fetcher,
            execution_resolver,
            lambda *_args: None,
        ),
    )

    print("PASS invalid descriptor rejected")


def test_task_engine_result_contract_rejected():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_execution_adapter_v0_1_result_contract",
    )

    task = {
        "id": 605,
        "goal_id": "goal-605",
        "description": "Bad result",
        "priority": 1,
    }

    descriptor = {
        "project": "project",
        "changes_file": "changes.json",
        "operation": "apply",
    }

    def task_fetcher(_task_id):
        return task.copy()

    def execution_resolver(_task_id):
        return descriptor.copy()

    def bad_start_task(
        _task_id,
        _project,
        _changes_file,
        _operation,
    ):
        return {"ok": True}

    assert_raises(
        TypeError,
        lambda: adapter.run_task_with_agent_execution(
            605,
            task_fetcher,
            execution_resolver,
            bad_start_task,
        ),
    )

    print("PASS Task Engine result contract enforced")


def test_agent_retry_is_disabled():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_execution_adapter_v0_1_retry",
    )

    assert_raises(
        ValueError,
        lambda: adapter.build_agent_controller_for_execution(
            task_id=606,
            task_fetcher=lambda _id: {
                "id": 606,
                "goal_id": "goal-606",
                "description": "Retry guard",
            },
            execution_resolver=lambda _id: {
                "project": "project",
                "changes_file": "changes.json",
                "operation": "apply",
            },
            start_task=lambda *_args: (
                {
                    "ok": True,
                    "task_status": "completed",
                },
                0,
            ),
            max_retries=1,
        ),
    )

    print("PASS AgentController retry guard")


def test_adapter_is_database_free():
    adapter = load_module(
        ADAPTER,
        "anu_agent_task_execution_adapter_v0_1_db",
    )

    assert not hasattr(adapter, "sqlite3")

    print("PASS execution adapter remains database-free")


if __name__ == "__main__":
    print(
        "===== ANU V0.5.6 AGENT EXECUTION ADAPTER REGRESSION ====="
    )

    tests = [
        test_successful_task_engine_delegation,
        test_failed_task_engine_result_propagates,
        test_missing_execution_descriptor_rejected,
        test_invalid_execution_descriptor_rejected,
        test_task_engine_result_contract_rejected,
        test_agent_retry_is_disabled,
        test_adapter_is_database_free,
    ]

    for test in tests:
        test()

    print(
        "AGENT_EXECUTION_ADAPTER_TEST_COMPLETE"
    )
