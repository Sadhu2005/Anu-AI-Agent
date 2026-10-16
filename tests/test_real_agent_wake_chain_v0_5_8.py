#!/usr/bin/env python3

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BASE = REPO
SCHEDULER = BASE / "bin" / "anu-task-scheduler-v0.1.py"
TASK = {
    "id": 1901,
    "goal_id": "goal-1901",
    "description": "V0.5.8 real scheduler agent chain",
    "priority": 10,
    "task_key": "TASK-1901",
    "title": "V0.5.8 chain",
    "parent_task_id": None,
    "status": "pending",
    "attempts": 0,
}

DESCRIPTOR = {
    "project": "sandbox-test-project",
    "changes_file": "changes.json",
    "operation": "apply",
}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDB:
    def execute(self, *args, **kwargs):
        return None


class FakeEngine:
    @staticmethod
    def fetch_task(task_id):
        assert task_id == TASK["id"]
        return TASK.copy()

    @staticmethod
    def start_task(
        task_id,
        project,
        changes_file,
        operation,
    ):
        assert task_id == TASK["id"]
        assert project == DESCRIPTOR["project"]
        assert changes_file == DESCRIPTOR["changes_file"]
        assert operation == DESCRIPTOR["operation"]

        return (
            {
                "ok": True,
                "task_id": task_id,
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
    def fetch_execution_descriptor(db, task_id):
        assert isinstance(db, FakeDB)
        assert task_id == TASK["id"]
        return DESCRIPTOR.copy()


def test_real_scheduler_to_agent_controller_chain():
    scheduler = load_module(
        SCHEDULER,
        "anu_task_scheduler_real_agent_chain_v0_5_8",
    )

    db = FakeDB()

    def load_real_runtime():
        runtime = load_module(
            BASE / "bin" / "anu-agent-task-execution-runtime-v0.1.py",
            "anu_agent_task_execution_runtime_real_chain_v0_5_8",
        )

        # Runtime modules hard-code /srv/anu; point them at this tree so
        # staging copies and the live checkout both resolve correctly.
        runtime.ADAPTER_MODULE = BASE / "bin" / "anu-agent-task-execution-adapter-v0.1.py"
        runtime.TASK_ADAPTER_MODULE = BASE / "bin" / "anu-agent-task-adapter-v0.1.py"
        runtime.TASK_ENGINE_MODULE = BASE / "bin" / "anu-task-engine-v0.5.3.py"
        runtime.EXECUTION_SPEC_MODULE = BASE / "bin" / "anu-task-execution-spec-v0.1.py"
        runtime._load_task_engine = lambda: FakeEngine()
        runtime._load_execution_spec = lambda: FakeExecutionSpec()

        return runtime

    original_runtime_loader = scheduler._load_agent_execution_runtime

    try:
        scheduler._load_agent_execution_runtime = load_real_runtime

        handler = (
            scheduler.build_production_agent_wake_execution_handler(db)
        )

        report = handler(
            {
                "action": "WAKE",
                "state": "READY",
                "task_id": TASK["id"],
            }
        )

        assert report["goal_id"] == TASK["goal_id"]
        assert report["state"] == "COMPLETE"
        assert report["terminal"] is True
        assert report["attempts"] == 1
        assert report["result_count"] == 1

        result = report["results"][0]

        assert result["action_id"] == f"task-action-{TASK['id']}"
        assert result["success"] is True
        assert result["status"] == "completed"

        assert result["data"]["task_id"] == TASK["id"]
        assert result["data"]["exit_code"] == 0
        assert (
            result["data"]["payload"]["task_status"]
            == "completed"
        )

        print("PASS real Scheduler -> AgentController chain")

    finally:
        scheduler._load_agent_execution_runtime = (
            original_runtime_loader
        )


if __name__ == "__main__":
    print(
        "===== ANU V0.5.8 REAL AGENT WAKE CHAIN REGRESSION ====="
    )

    test_real_scheduler_to_agent_controller_chain()

    print("V0.5.8_REAL_AGENT_WAKE_CHAIN_TEST_COMPLETE")
