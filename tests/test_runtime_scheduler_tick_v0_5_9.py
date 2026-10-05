#!/usr/bin/env python3

import importlib.util
from pathlib import Path


BASE = Path("/srv/anu")
RUNTIME = BASE / "bin" / "anu-runtime.py"


def load_runtime():
    spec = importlib.util.spec_from_file_location(
        "anu_runtime_v0_5_9_test",
        RUNTIME,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load runtime: {RUNTIME}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normal_status_runs_agent_scheduler():
    runtime = load_runtime()

    observed = {}

    runtime.write_log = lambda message: observed.setdefault(
        "logs", []
    ).append(message)

    class FakeScheduler:
        @staticmethod
        def run_agent_scheduler_cycle_once(
            db_path,
            max_tasks,
            lock_path=None,
        ):
            observed["db_path"] = db_path
            observed["max_tasks"] = max_tasks
            observed["lock_path"] = lock_path
            return []

    runtime._load_scheduler = lambda: FakeScheduler()

    result = runtime.run_agent_scheduler_cycle(
        {
            "status": "NORMAL",
        }
    )

    assert result == []
    assert observed["db_path"] == str(runtime.DB_FILE)
    assert observed["max_tasks"] == 1
    assert observed["lock_path"] == str(
        runtime.SCHEDULER_LOCK_FILE
    )

    print("PASS NORMAL resource state runs agent scheduler")


def test_non_normal_status_skips_scheduler():
    runtime = load_runtime()

    observed = {
        "loaded": False,
    }

    runtime.write_log = lambda message: observed.update(
        loaded=True
    )

    def fail_loader():
        raise AssertionError(
            "scheduler must not load when resources are not NORMAL"
        )

    runtime._load_scheduler = fail_loader

    result = runtime.run_agent_scheduler_cycle(
        {
            "status": "PAUSED",
        }
    )

    assert result == {
        "status": "SKIPPED_RESOURCE",
        "resource_status": "PAUSED",
    }

    assert observed["loaded"] is True

    print("PASS non-NORMAL resource state skips scheduler")


def test_scheduler_exception_is_contained():
    runtime = load_runtime()

    messages = []
    runtime.write_log = messages.append

    class FailingScheduler:
        @staticmethod
        def run_agent_scheduler_cycle_once(**kwargs):
            raise RuntimeError("synthetic scheduler failure")

    runtime._load_scheduler = lambda: FailingScheduler()

    result = runtime.run_agent_scheduler_cycle(
        {
            "status": "NORMAL",
        }
    )

    assert result is None
    assert any(
        "Scheduler cycle failed" in message
        for message in messages
    )

    print("PASS scheduler failure is contained")


def test_only_normal_is_execution_allowed():
    runtime = load_runtime()

    assert runtime.scheduler_execution_allowed(
        {"status": "NORMAL"}
    )

    assert not runtime.scheduler_execution_allowed(
        {"status": "WARNING"}
    )

    assert not runtime.scheduler_execution_allowed(
        {"status": "PAUSE"}
    )

    assert not runtime.scheduler_execution_allowed(
        {"status": "DENY"}
    )

    assert not runtime.scheduler_execution_allowed({})

    print("PASS scheduler execution gate is NORMAL-only")


if __name__ == "__main__":
    print("===== ANU V0.5.9 RUNTIME SCHEDULER TICK REGRESSION =====")

    test_normal_status_runs_agent_scheduler()
    test_non_normal_status_skips_scheduler()
    test_scheduler_exception_is_contained()
    test_only_normal_is_execution_allowed()

    print("RUNTIME_SCHEDULER_TICK_TEST_COMPLETE")
