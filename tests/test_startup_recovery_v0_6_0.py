#!/usr/bin/env python3

import fcntl
import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "bin" / "anu-runtime.py"
TASK_ENGINE = REPO / "bin" / "anu-task-engine-v0.5.3.py"
RECOVERY = REPO / "bin" / "anu-recovery-v0.1.py"
SCHEMA = REPO / "migrations" / "0001_initial.sql"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quiet_runtime(name):
    runtime = load(RUNTIME, name)
    runtime.messages = []
    runtime.write_log = runtime.messages.append
    runtime.save_state = lambda data: None
    runtime.run_resource_guardian = lambda: {"status": "NORMAL", "memory": {}}
    return runtime


class Scheduler:
    def __init__(self):
        self.cycles = 0

    def run_agent_scheduler_cycle_once(self, db_path, max_tasks, lock_path=None):
        self.cycles += 1
        return []


class Recovery:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def recover(self, db_path=None, lock_path=None):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_no_scheduling_until_recovery_has_run():
    runtime = quiet_runtime("rt_startup_1")
    scheduler = Scheduler()
    recovery = Recovery(
        {"status": "DEFERRED_ACTIVE_EXECUTION"},
        RuntimeError("synthetic recovery failure"),
        {"status": "RECOVERED",
         "transactions": [{"transaction_id": "mut-x", "status": "INTERRUPTED_ROLLED_BACK",
                           "reason": "applied change restored from checkpoint"}],
         "tasks": [{"task_id": 5, "previous_status": "running", "status": "failed",
                    "reason": "interrupted"}]},
    )
    runtime._load_scheduler = lambda: scheduler
    runtime._load_recovery = lambda: recovery

    assert runtime.run_runtime_iteration() == {"status": "SKIPPED_RECOVERY_PENDING"}
    assert "Recovery deferred: execution in progress" in runtime.messages
    assert runtime.run_runtime_iteration() == {"status": "SKIPPED_RECOVERY_PENDING"}
    assert any(m.startswith("Recovery failed: RuntimeError") for m in runtime.messages)
    assert scheduler.cycles == 0 and runtime.recovery_done is False

    assert runtime.run_runtime_iteration() == []
    assert runtime.recovery_done is True and scheduler.cycles == 1
    assert "Recovery RECOVERED: transactions=1 tasks=1" in runtime.messages
    assert any("Recovery task 5: running -> failed (interrupted)" in m for m in runtime.messages)

    assert runtime.run_runtime_iteration() == []
    assert recovery.calls == 3 and scheduler.cycles == 2, "recovery runs once per runtime start"

    print("PASS runtime schedules nothing until recovery succeeds, then recovers only once")


def engine_env(root):
    engine = load(TASK_ENGINE, f"engine_lost_{root.name}")
    rec = load(RECOVERY, f"rec_engine_{root.name}")
    engine.DB_FILE = rec.DB_FILE = root / "brain.db"
    engine.LOG_FILE = root / "logs" / "engine.jsonl"
    rec.LOG_FILE = root / "logs" / "recovery.jsonl"
    engine.EXECUTION_LOCK_FILE = rec.EXECUTION_LOCK_FILE = root / "execution.lock"
    rec.CHECKPOINT_ROOT = root / "checkpoints"
    rec.WORKSPACE = root / "projects"
    engine._load_recovery = lambda: rec

    dies_silently = root / "dies.py"
    dies_silently.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    engine.SAFE_MUTATION = dies_silently

    with sqlite3.connect(engine.DB_FILE) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
        db.execute("ALTER TABLE mutation_transactions ADD COLUMN idempotency_key TEXT")
        task_id = db.execute(
            "INSERT INTO tasks (title, status, attempts) VALUES ('lost', 'pending', 0)"
        ).lastrowid
    return engine, task_id


def events(engine, task_id):
    with sqlite3.connect(engine.DB_FILE) as db:
        return [r[0] for r in db.execute(
            "SELECT event_type FROM agent_events WHERE task_id = ? ORDER BY id", (task_id,)
        )]


def test_engine_recovers_a_lost_mutation_immediately():
    with tempfile.TemporaryDirectory() as tmp:
        engine, task_id = engine_env(Path(tmp))

        result, code = engine._run_mutation_once(
            task_id, "demo", "/nonexistent.json", "python_test", 1, run_id="run-lost"
        )

        assert code == 6, code
        assert result["reason"] == "invalid_mutation_engine_response", result
        assert result["task_status"] == "failed", result
        assert result["retryable"] is True
        assert result["recovery"]["status"] == "RECOVERED", result["recovery"]
        assert engine.fetch_task(task_id)["status"] == "failed"

        seen = events(engine, task_id)
        assert "TASK_MUTATION_LOST" in seen and "RECOVERY_TASK" in seen, seen
        assert "TASK_BLOCKED" not in seen, seen

    print("PASS Task Engine: a silently dead Safe Mutation is recovered at once (task failed, retryable)")


def test_engine_blocks_when_recovery_cannot_run():
    with tempfile.TemporaryDirectory() as tmp:
        engine, task_id = engine_env(Path(tmp))

        fd = os.open(engine.EXECUTION_LOCK_FILE, os.O_RDONLY | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            result, code = engine._run_mutation_once(
                task_id, "demo", "/nonexistent.json", "python_test", 1, run_id="run-busy"
            )
        finally:
            os.close(fd)

        assert result["recovery"] == {"status": "DEFERRED_ACTIVE_EXECUTION"}, result
        assert result["task_status"] == "blocked", result
        assert result["retryable"] is False

        with sqlite3.connect(engine.DB_FILE) as db:
            meta = json.loads(db.execute(
                "SELECT metadata FROM agent_events WHERE task_id = ? AND event_type = 'TASK_BLOCKED'",
                (task_id,),
            ).fetchone()[0])
        assert meta["reason"] == "invalid_mutation_engine_response", meta

    print("PASS Task Engine: if another execution blocks recovery, the task is blocked (fail closed)")


if __name__ == "__main__":
    print("===== ANU V0.6.0 STARTUP AND LOST-MUTATION RECOVERY REGRESSION =====")

    test_no_scheduling_until_recovery_has_run()
    test_engine_recovers_a_lost_mutation_immediately()
    test_engine_blocks_when_recovery_cannot_run()

    print("STARTUP_RECOVERY_TEST_COMPLETE")
