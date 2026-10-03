#!/usr/bin/env python3

import copy
import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"
TEST_PLAN = [
    {
        "task_id": 201,
        "action": "WAKE",
        "current_status": "pending",
        "current_attempts": 0,
        "state": "READY",
        "reason": "ALL_DEPENDENCIES_COMPLETED",
    },
    {
        "task_id": 202,
        "action": "WAIT",
        "current_status": "pending",
        "current_attempts": 0,
        "state": "WAITING",
        "reason": "PREREQUISITES_INCOMPLETE",
    },
    {
        "task_id": 203,
        "action": "BLOCK",
        "current_status": "pending",
        "current_attempts": 0,
        "state": "BLOCKED",
        "reason": "PREREQUISITE_TERMINALLY_UNAVAILABLE",
    },
]


def load_scheduler():
    spec = importlib.util.spec_from_file_location(
        "anu_task_scheduler_v0_1_consumer_test",
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
    print("===== ANU V0.5.4 DEPENDENCY WAKE CONSUMER REGRESSION =====")

    scheduler = load_scheduler()

    original_plan = copy.deepcopy(TEST_PLAN)
    calls = []

    def wake_handler(item):
        calls.append(("WAKE", item["task_id"]))
        return {
            "accepted": True,
            "action": "WAKE",
            "task_id": item["task_id"],
        }

    def wait_handler(item):
        calls.append(("WAIT", item["task_id"]))
        return {
            "accepted": True,
            "action": "WAIT",
            "task_id": item["task_id"],
        }

    def block_handler(item):
        calls.append(("BLOCK", item["task_id"]))
        return {
            "accepted": True,
            "action": "BLOCK",
            "task_id": item["task_id"],
        }

    handlers = {
        "WAKE": wake_handler,
        "WAIT": wait_handler,
        "BLOCK": block_handler,
    }

    try:
        results = scheduler.consume_dependency_wake_plan(
            TEST_PLAN,
            handlers,
        )
    except AttributeError:
        print("EXPECTED_RED: consume_dependency_wake_plan is not implemented")
        raise

    assert_equal(
        calls,
        [
            ("WAKE", 201),
            ("WAIT", 202),
            ("BLOCK", 203),
        ],
        "handler routing must preserve deterministic plan ordering",
    )
    print("PASS deterministic action routing")

    assert_equal(
        results,
        [
            {
                "accepted": True,
                "action": "WAKE",
                "task_id": 201,
            },
            {
                "accepted": True,
                "action": "WAIT",
                "task_id": 202,
            },
            {
                "accepted": True,
                "action": "BLOCK",
                "task_id": 203,
            },
        ],
        "consumer must return handler results in plan order",
    )
    print("PASS handler results preserve ordering")

    assert_equal(
        TEST_PLAN,
        original_plan,
        "consumer must not mutate the dependency plan",
    )
    print("PASS consumer does not mutate plan")

    bad_plan = copy.deepcopy(TEST_PLAN)
    bad_plan[0]["action"] = "UNKNOWN"

    try:
        scheduler.consume_dependency_wake_plan(
            bad_plan,
            handlers,
        )
    except ValueError:
        print("PASS unknown action rejected")
    else:
        raise AssertionError("unknown action must raise ValueError")

    isolated_plan = copy.deepcopy(TEST_PLAN)
    isolated_plan[0]["metadata"] = {
        "tags": ["original"],
    }

    def mutating_handler(item):
        item["metadata"]["tags"].append("mutated")
        return {
            "accepted": True,
            "action": item["action"],
            "task_id": item["task_id"],
        }

    isolated_handlers = {
        "WAKE": mutating_handler,
        "WAIT": wait_handler,
        "BLOCK": block_handler,
    }

    scheduler.consume_dependency_wake_plan(
        isolated_plan,
        isolated_handlers,
    )

    assert_equal(
        isolated_plan[0]["metadata"]["tags"],
        ["original"],
        "handler must not mutate nested caller-owned plan data",
    )
    print("PASS handler receives isolated deep copy")

    partial_calls = []

    def recording_wake(item):
        partial_calls.append(("WAKE", item["task_id"]))
        return {"accepted": True}

    def recording_wait(item):
        partial_calls.append(("WAIT", item["task_id"]))
        return {"accepted": True}

    bad_action_plan = [
        copy.deepcopy(TEST_PLAN[0]),
        copy.deepcopy(TEST_PLAN[1]),
    ]
    bad_action_plan[1]["action"] = "UNKNOWN"

    preflight_handlers = {
        "WAKE": recording_wake,
        "WAIT": recording_wait,
        "BLOCK": block_handler,
    }

    try:
        scheduler.consume_dependency_wake_plan(
            bad_action_plan,
            preflight_handlers,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("invalid action must raise ValueError")

    assert_equal(
        partial_calls,
        [],
        "invalid plan must be rejected before any handler executes",
    )
    print("PASS invalid action causes no partial execution")

    missing_handler_calls = []

    def only_wake(item):
        missing_handler_calls.append(("WAKE", item["task_id"]))
        return {"accepted": True}

    try:
        scheduler.consume_dependency_wake_plan(
            copy.deepcopy(TEST_PLAN),
            {"WAKE": only_wake},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("missing handler must raise ValueError")

    assert_equal(
        missing_handler_calls,
        [],
        "missing handler must be detected before execution",
    )
    print("PASS missing handler causes no partial execution")

    print("DEPENDENCY_WAKE_CONSUMER_TEST_COMPLETE")


if __name__ == "__main__":
    main()
