#!/usr/bin/env python3

import copy
import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"


def load_scheduler():
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_wake_execution_test",
        SCHEDULER,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load scheduler: {SCHEDULER}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            f"{message}\nexpected={expected!r}\nactual={actual!r}"
        )


def main():
    print(
        "===== ANU V0.5.4 WAKE EXECUTION ADAPTER REGRESSION ====="
    )

    scheduler = load_scheduler()

    calls = []

    def start_task(
        task_id,
        project,
        changes_file,
        operation,
    ):
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
            },
            0,
        )

    def execution_resolver(task_id):
        assert_equal(
            task_id,
            301,
            "execution resolver must receive the planned task_id",
        )
        return {
            "project": "sandbox-test-project",
            "changes_file": "/tmp/wake-301.json",
            "operation": "python_test",
        }

    plan_item = {
        "task_id": 301,
        "action": "WAKE",
        "current_status": "pending",
        "current_attempts": 0,
        "state": "READY",
        "reason": "ALL_DEPENDENCIES_COMPLETED",
    }

    original_item = copy.deepcopy(plan_item)

    try:
        handler = scheduler.build_wake_execution_handler(
            start_task,
            execution_resolver,
        )
    except AttributeError:
        print(
            "EXPECTED_RED: build_wake_execution_handler is not implemented"
        )
        raise

    result = handler(plan_item)

    assert_equal(
        calls,
        [
            (
                301,
                "sandbox-test-project",
                "/tmp/wake-301.json",
                "python_test",
            )
        ],
        "WAKE adapter must call start_task with explicit execution inputs",
    )
    print("PASS WAKE routes to start_task")

    assert_equal(
        result,
        (
            {
                "ok": True,
                "task_id": 301,
                "task_status": "completed",
            },
            0,
        ),
        "WAKE adapter must preserve the start_task return contract",
    )
    print("PASS WAKE preserves engine return contract")

    assert_equal(
        plan_item,
        original_item,
        "WAKE adapter must not mutate the plan item",
    )
    print("PASS WAKE adapter does not mutate plan item")

    invalid_item = copy.deepcopy(plan_item)
    invalid_item["action"] = "WAIT"

    calls_before = list(calls)

    try:
        handler(invalid_item)
    except ValueError:
        print("PASS non-WAKE action rejected")
    else:
        raise AssertionError(
            "non-WAKE action must raise ValueError"
        )

    assert_equal(
        calls,
        calls_before,
        "rejected action must not invoke start_task",
    )
    print("PASS rejected action does not invoke engine")

    not_ready_item = copy.deepcopy(plan_item)
    not_ready_item["state"] = "WAITING"

    try:
        handler(not_ready_item)
    except ValueError:
        print("PASS non-READY dependency state rejected")
    else:
        raise AssertionError(
            "WAKE must require dependency state READY"
        )

    assert_equal(
        calls,
        calls_before,
        "non-READY action must not invoke start_task",
    )
    print("PASS non-READY action does not invoke engine")

    missing_execution = {
        "task_id": 302,
        "action": "WAKE",
        "state": "READY",
    }

    def missing_resolver(task_id):
        return None

    missing_handler = scheduler.build_wake_execution_handler(
        start_task,
        missing_resolver,
    )

    calls_before = list(calls)

    try:
        missing_handler(missing_execution)
    except ValueError:
        print("PASS missing execution descriptor rejected")
    else:
        raise AssertionError(
            "missing execution descriptor must raise ValueError"
        )

    assert_equal(
        calls,
        calls_before,
        "missing execution descriptor must not invoke start_task",
    )
    print("PASS missing execution does not invoke engine")

    incomplete_calls = []

    def incomplete_start_task(*args, **kwargs):
        incomplete_calls.append((args, kwargs))
        raise AssertionError(
            "start_task must not execute with incomplete descriptor"
        )

    def incomplete_resolver(task_id):
        return {
            "project": "sandbox-test-project",
        }

    incomplete_handler = scheduler.build_wake_execution_handler(
        incomplete_start_task,
        incomplete_resolver,
    )

    try:
        incomplete_handler(plan_item)
    except ValueError:
        print("PASS incomplete execution descriptor rejected")
    else:
        raise AssertionError(
            "incomplete execution descriptor must raise ValueError"
        )

    assert_equal(
        incomplete_calls,
        [],
        "incomplete descriptor must not invoke start_task",
    )
    print("PASS incomplete descriptor does not invoke engine")

    print(
        "DEPENDENCY_WAKE_EXECUTION_ADAPTER_TEST_COMPLETE"
    )


if __name__ == "__main__":
    main()
