#!/usr/bin/env python3

import importlib.util
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TASK_ENGINE = REPO / "bin" / "anu-task-engine-v0.5.3.py"
RECOVERY = REPO / "bin" / "anu-recovery-v0.1.py"
SCHEDULER = REPO / "bin" / "anu-task-scheduler-v0.1.py"
TASK_GRAPH = REPO / "bin" / "anu-task-graph-v0.1.py"
SCHEMA = REPO / "migrations" / "0001_initial.sql"

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def new_db(path, retry_columns=True):
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
        db.execute("ALTER TABLE mutation_transactions ADD COLUMN idempotency_key TEXT")
        if retry_columns:
            db.execute("ALTER TABLE tasks ADD COLUMN failure_class TEXT")
            db.execute("ALTER TABLE tasks ADD COLUMN retry_after TEXT")


def add_task(db, status="pending", attempts=0, retry_after=None, failure_class=None):
    return db.execute(
        """INSERT INTO tasks (title, status, attempts, retry_after, failure_class)
           VALUES ('retry case', ?, ?, ?, ?)""",
        (status, attempts, retry_after, failure_class),
    ).lastrowid


def test_classification_table():
    rec = load(RECOVERY, "rec_classes")
    engine = load(TASK_ENGINE, "engine_classes")

    assert engine.FAILURE_CLASSES == rec.FAILURE_CLASSES, "engine and recovery classes diverged"
    assert engine.MAX_ATTEMPTS == rec.MAX_ATTEMPTS == 3

    cases = {
        "COMMITTED": ("completed", None),
        "ALREADY_COMMITTED": ("completed", None),
        "ROLLED_BACK": ("failed", "code_defect"),
        "ABORTED_BASELINE_FAILURE": ("blocked", "environment"),
        "MUTATION_ENGINE_ERROR": ("blocked", "contract"),
        "ROLLBACK_VERIFICATION_FAILED": ("blocked", "integrity"),
        "INTERRUPTED_CONFLICT": ("blocked", "integrity"),
        "SOMETHING_UNKNOWN": ("blocked", "integrity"),
        None: ("pending", "interrupted"),
        "INTERRUPTED_NOT_APPLIED": ("pending", "interrupted"),
        "INTERRUPTED_ROLLED_BACK": ("pending", "interrupted"),
    }
    for mutation_status, expected in cases.items():
        status, failure_class, retry_after, _ = rec.task_outcome(mutation_status, 1, NOW)
        assert (status, failure_class) == expected, (mutation_status, status, failure_class)
        assert (retry_after is not None) == (status == "pending"), (mutation_status, retry_after)

    assert engine.classify_mutation({"status": "ROLLED_BACK"}, 1) == "failed"

    print("PASS every outcome in section 3.4 maps to its status and failure class; "
          "only interrupted work is retried")


def test_backoff_and_attempt_limit():
    rec = load(RECOVERY, "rec_backoff")

    expected = {
        0: NOW + timedelta(minutes=2),
        1: NOW + timedelta(minutes=2),
        2: NOW + timedelta(minutes=10),
    }
    for attempts, due in expected.items():
        status, failure_class, retry_after, _ = rec.task_outcome(None, attempts, NOW)
        assert status == "pending" and failure_class == "interrupted", attempts
        assert datetime.fromisoformat(retry_after) == due, (attempts, retry_after)

    for attempts in (3, 4):
        status, failure_class, retry_after, reason = rec.task_outcome(
            "INTERRUPTED_ROLLED_BACK", attempts, NOW
        )
        assert (status, failure_class, retry_after) == ("blocked", "interrupted", None), attempts
        assert "attempts used" in reason

    print("PASS backoff is 2 then 10 minutes; after 3 attempts interrupted work is blocked")


def test_scheduler_honours_retry_after():
    scheduler = load(SCHEDULER, "sched_retry")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "brain.db"
        new_db(path)
        with sqlite3.connect(path) as db:
            fresh = add_task(db)
            due = add_task(db, retry_after=(NOW - timedelta(seconds=1)).isoformat())
            waiting = add_task(db, retry_after=(NOW + timedelta(minutes=2)).isoformat())
            unreadable = add_task(db, retry_after="not-a-time")
            add_task(db, status="failed", attempts=1, failure_class="code_defect")
            add_task(db, status="blocked", attempts=3, failure_class="interrupted")

        with sqlite3.connect(path) as db:
            found = [c["task_id"] for c in scheduler.discover_ready_tasks(db, now=NOW)]
            assert found == [fresh, due], found

            later = NOW + timedelta(minutes=3)
            found = [c["task_id"] for c in scheduler.discover_ready_tasks(db, now=later)]
            assert found == [fresh, due, waiting], found
            assert unreadable not in found, "an unreadable retry_after must fail closed"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "brain.db"
        new_db(path, retry_columns=False)
        with sqlite3.connect(path) as db:
            legacy = add_task_without_retry_columns(db)
            found = [c["task_id"] for c in scheduler.discover_ready_tasks(db, now=NOW)]
        assert found == [legacy], found

    print("PASS scheduler skips a pending task until retry_after passes, and works "
          "on a schema without the new columns")


def add_task_without_retry_columns(db):
    return db.execute(
        "INSERT INTO tasks (title, status, attempts) VALUES ('legacy', 'pending', 0)"
    ).lastrowid


def engine_env(root, safe_mutation_source):
    engine = load(TASK_ENGINE, f"engine_retry_{root.name}")
    rec = load(RECOVERY, f"rec_retry_{root.name}")
    engine.DB_FILE = rec.DB_FILE = root / "brain.db"
    engine.LOG_FILE = root / "logs" / "engine.jsonl"
    rec.LOG_FILE = root / "logs" / "recovery.jsonl"
    engine.EXECUTION_LOCK_FILE = rec.EXECUTION_LOCK_FILE = root / "execution.lock"
    engine.TASK_GRAPH = TASK_GRAPH
    rec.CHECKPOINT_ROOT = root / "checkpoints"
    rec.WORKSPACE = root / "projects"
    engine._load_recovery = lambda: rec

    fake = root / "safe_mutation.py"
    fake.write_text(safe_mutation_source, encoding="utf-8")
    engine.SAFE_MUTATION = fake

    new_db(engine.DB_FILE, retry_columns=False)
    with sqlite3.connect(engine.DB_FILE) as db:
        task_id = add_task_without_retry_columns(db)
    return engine, task_id


DIES_SILENTLY = "import sys\nsys.exit(1)\n"


def retry_state(engine, task_id):
    with sqlite3.connect(engine.DB_FILE) as db:
        return db.execute(
            "SELECT status, attempts, failure_class, retry_after FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()


def test_crash_loop_ends_blocked_after_three_attempts():
    scheduler = load(SCHEDULER, "sched_crash_loop")

    with tempfile.TemporaryDirectory() as tmp:
        engine, task_id = engine_env(Path(tmp), DIES_SILENTLY)

        backoffs = []
        for attempt in (1, 2, 3):
            result, _ = engine.start_task(task_id, "demo", "/nonexistent.json", "python_test")
            status, attempts, failure_class, retry_after = retry_state(engine, task_id)
            assert attempts == attempt and failure_class == "interrupted", (attempt, result)

            if attempt < 3:
                assert status == "pending" and result["retryable"] is True, result
                due = datetime.fromisoformat(retry_after)
                backoffs.append(due)

                with sqlite3.connect(engine.DB_FILE) as db:
                    now = datetime.now(timezone.utc)
                    ids = [c["task_id"] for c in scheduler.discover_ready_tasks(db, now=now)]
                    assert task_id not in ids, "picked up before its backoff elapsed"
                    ids = [c["task_id"] for c in scheduler.discover_ready_tasks(db, now=due)]
                    assert task_id in ids, "not picked up once its backoff elapsed"
            else:
                assert status == "blocked" and retry_after is None, (status, retry_after)
                assert result["retryable"] is False, result

        gap_one = backoffs[0] - datetime.now(timezone.utc)
        gap_two = backoffs[1] - datetime.now(timezone.utc)
        assert timedelta(minutes=1) < gap_one <= timedelta(minutes=2), gap_one
        assert timedelta(minutes=9) < gap_two <= timedelta(minutes=10), gap_two

        with sqlite3.connect(engine.DB_FILE) as db:
            decisions = [json.loads(r[0]) for r in db.execute(
                "SELECT metadata FROM agent_events WHERE task_id = ? "
                "AND event_type = 'RECOVERY_TASK' ORDER BY id", (task_id,)
            )]
        assert [d["status"] for d in decisions] == ["pending", "pending", "blocked"], decisions

    print("PASS a crash loop retries after 2 and 10 minutes, then ends blocked "
          "with failure_class=interrupted")


COMMITS = (
    "import json\n"
    "print(json.dumps({'ok': True, 'status': 'COMMITTED', 'transaction_id': 'mut-ok'}))\n"
)


def test_successful_retry_clears_the_failure_class():
    with tempfile.TemporaryDirectory() as tmp:
        engine, task_id = engine_env(Path(tmp), COMMITS)
        with sqlite3.connect(engine.DB_FILE) as db:
            db.execute("ALTER TABLE tasks ADD COLUMN failure_class TEXT")
            db.execute("ALTER TABLE tasks ADD COLUMN retry_after TEXT")
            db.execute(
                "UPDATE tasks SET attempts = 1, failure_class = 'interrupted', "
                "retry_after = ? WHERE id = ?",
                ((NOW - timedelta(minutes=1)).isoformat(), task_id),
            )

        result, code = engine.start_task(task_id, "demo", "/nonexistent.json", "python_test")
        assert code == 0 and result["task_status"] == "completed", result
        assert retry_state(engine, task_id) == ("completed", 2, None, None)

    print("PASS a retry that commits completes the task and clears failure_class and retry_after")


if __name__ == "__main__":
    print("===== ANU V0.6.0 RETRY CLASSIFICATION REGRESSION =====")

    test_classification_table()
    test_backoff_and_attempt_limit()
    test_scheduler_honours_retry_after()
    test_crash_loop_ends_blocked_after_three_attempts()
    test_successful_retry_clears_the_failure_class()

    print("RETRY_CLASSIFICATION_TEST_COMPLETE")
