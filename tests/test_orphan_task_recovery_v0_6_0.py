#!/usr/bin/env python3

import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RECOVERY = REPO / "bin" / "anu-recovery-v0.1.py"
SCHEMA = REPO / "migrations" / "0001_initial.sql"

T0 = "2026-10-15T10:00:00.000000+00:00"
T1 = "2026-10-15T10:00:01.000000+00:00"
T_OLD = "2026-10-15T09:00:00.000000+00:00"


def load_recovery(root):
    spec = importlib.util.spec_from_file_location(f"rec_tasks_{root.name}", RECOVERY)
    rec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rec)
    rec.DB_FILE = root / "brain.db"
    rec.LOG_FILE = root / "logs" / "recovery.jsonl"
    rec.CHECKPOINT_ROOT = root / "checkpoints"
    rec.WORKSPACE = root / "projects"
    rec.EXECUTION_LOCK_FILE = root / "execution.lock"
    with sqlite3.connect(rec.DB_FILE) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
        db.execute("ALTER TABLE mutation_transactions ADD COLUMN idempotency_key TEXT")
    return rec


def add_task(db, status, attempts=1, planning_at=T0, run_id=None):
    task_id = db.execute(
        "INSERT INTO tasks (title, status, attempts) VALUES (?, ?, ?)",
        (f"task in {status}", status, attempts),
    ).lastrowid
    if planning_at:
        db.execute(
            """INSERT INTO agent_events
               (task_id, run_id, attempt, event_type, message, metadata, created_at)
               VALUES (?, ?, ?, 'TASK_STATE', 'planning', '{}', ?)""",
            (task_id, run_id or f"run-{task_id}", attempts, planning_at),
        )
    return task_id


def add_tx(db, task_id, status, created_at=T1):
    tx_id = f"mut-{task_id}-{status.lower()}-{created_at[-15:-6]}"
    db.execute(
        """INSERT INTO mutation_transactions
           (project, file_path, operation, status, created_at, transaction_id,
            task_id, details)
           VALUES ('demo', 'app.py', 'python_test', ?, ?, ?, ?, '{}')""",
        (status, created_at, tx_id, task_id),
    )
    return tx_id


def task_status(rec, task_id):
    with sqlite3.connect(rec.DB_FILE) as db:
        return db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]


def task_retry(rec, task_id):
    with sqlite3.connect(rec.DB_FILE) as db:
        return db.execute(
            "SELECT failure_class, retry_after FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()


def test_task_outcomes_follow_the_transaction():
    with tempfile.TemporaryDirectory() as tmp:
        rec = load_recovery(Path(tmp))

        with sqlite3.connect(rec.DB_FILE) as db:
            cases = {
                "committed": (add_task(db, "running"), "COMMITTED", "completed"),
                "already": (add_task(db, "verifying"), "ALREADY_COMMITTED", "completed"),
                "rolled_back": (add_task(db, "verifying"), "ROLLED_BACK", "failed"),
                "started": (add_task(db, "running"), "STARTED", "pending"),
                "baseline": (add_task(db, "verifying"), "ABORTED_BASELINE_FAILURE", "blocked"),
                "engine_error": (add_task(db, "verifying"), "MUTATION_ENGINE_ERROR", "blocked"),
                "rollback_failed": (add_task(db, "verifying"), "ROLLBACK_VERIFICATION_FAILED", "blocked"),
            }
            for task_id, tx_status, _ in cases.values():
                add_tx(db, task_id, tx_status)
            no_tx = add_task(db, "running")
            planning = add_task(db, "planning")
            ready = add_task(db, "ready")
            old_attempt = add_task(db, "running", attempts=2)
            add_tx(db, old_attempt, "COMMITTED", created_at=T_OLD)
            no_event = add_task(db, "running", planning_at=None)

        result = rec.recover()
        assert result["status"] == "RECOVERED", result

        for name, (task_id, tx_status, expected) in cases.items():
            actual = task_status(rec, task_id)
            assert actual == expected, (name, tx_status, actual)

        for task_id in (no_tx, planning, ready, old_attempt, no_event):
            assert task_status(rec, task_id) == "pending", task_id
            failure_class, retry_after = task_retry(rec, task_id)
            assert failure_class == "interrupted" and retry_after, (task_id, failure_class, retry_after)

        expected_classes = {
            "committed": None, "already": None, "rolled_back": "code_defect",
            "baseline": "environment", "engine_error": "contract",
            "rollback_failed": "integrity",
        }
        for name, failure_class in expected_classes.items():
            assert task_retry(rec, cases[name][0]) == (failure_class, None), name

        by_task = {t["task_id"]: t for t in result["tasks"]}
        assert by_task[cases["started"][0]]["mutation_status"] == "INTERRUPTED_NOT_APPLIED"
        assert by_task[cases["started"][0]]["reason"] == "interrupted; retry scheduled"
        assert by_task[no_tx]["failure_class"] == "interrupted"
        assert by_task[old_attempt]["transaction_id"] is None, "an older attempt's commit must not complete the task"

    print("PASS task outcomes: committed->completed, rolled back->failed (code_defect), "
          "interrupted->pending with backoff, other endings->blocked by class")


def test_terminal_and_pending_tasks_are_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        rec = load_recovery(Path(tmp))
        with sqlite3.connect(rec.DB_FILE) as db:
            ids = {s: add_task(db, s) for s in ("pending", "completed", "failed", "blocked")}

        result = rec.recover()
        assert result == {"status": "CLEAN", "transactions": [], "tasks": []}, result
        for status, task_id in ids.items():
            assert task_status(rec, task_id) == status

    print("PASS pending and terminal tasks are never touched")


def test_recovery_events_carry_run_and_attempt():
    with tempfile.TemporaryDirectory() as tmp:
        rec = load_recovery(Path(tmp))
        with sqlite3.connect(rec.DB_FILE) as db:
            task_id = add_task(db, "running", attempts=2, run_id="run-xyz")

        rec.recover()

        with sqlite3.connect(rec.DB_FILE) as db:
            db.row_factory = sqlite3.Row
            ev = db.execute(
                "SELECT * FROM agent_events WHERE event_type = 'RECOVERY_TASK'"
            ).fetchone()
        assert ev["task_id"] == task_id
        assert ev["run_id"] == "run-xyz"
        assert ev["attempt"] == 2
        meta = json.loads(ev["metadata"])
        assert meta["previous_status"] == "running" and meta["status"] == "pending", meta
        assert meta["failure_class"] == "interrupted" and meta["retry_after"], meta

        log = (Path(tmp) / "logs" / "recovery.jsonl").read_text(encoding="utf-8")
        assert '"event": "RECOVERY_TASK"' in log

    print("PASS RECOVERY_TASK event and log carry task, run, attempt, and transition")


if __name__ == "__main__":
    print("===== ANU V0.6.0 ORPHAN TASK RECOVERY REGRESSION =====")

    test_task_outcomes_follow_the_transaction()
    test_terminal_and_pending_tasks_are_untouched()
    test_recovery_events_carry_run_and_attempt()

    print("ORPHAN_TASK_RECOVERY_TEST_COMPLETE")
