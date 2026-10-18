#!/usr/bin/env python3
"""ANU Task Engine v0.5.1

Adds explicit retry semantics to the v0.3 task lifecycle.

Safety model:
- The Task Engine never executes raw shell commands against a project.
- Code changes and verification are delegated to Safe Mutation Engine v0.2.
- Failed mutations remain retryable until MAX_ATTEMPTS is reached.
- A retry must provide a new mutation plan; the engine never blindly re-runs
  a failed mutation plan.
- Blocked tasks are not automatically retried because the failure may be
  environmental/policy-related rather than a code defect.
- Interrupted work (V0.6.0) is the one exception: recovery returns it to
  pending with a retry_after backoff, re-running the same plan.
"""

import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DB_FILE = BASE / "brain" / "anu-brain.db"
SAFE_MUTATION = BASE / "bin" / "anu-safe-mutation-v0.2.py"
TASK_GRAPH = BASE / "bin" / "anu-task-graph-v0.1.py"
RECOVERY = BASE / "bin" / "anu-recovery-v0.1.py"
VERIFICATION = BASE / "bin" / "anu-verification-v0.1.py"
LOG_FILE = BASE / "logs" / "anu-task-engine.jsonl"
EXECUTION_LOCK_FILE = BASE / "runtime" / "anu-execution.lock"
_HERE = Path(__file__).resolve().parent


def _anu_db():
    for candidate in (
        BASE / "bin" / "anu-db-v0.1.py",
        _HERE / "anu-db-v0.1.py",
    ):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "anu_db_v0_1_task_engine",
                candidate,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError("anu-db-v0.1.py not found")

TERMINAL = {"completed", "blocked"}
RETRYABLE = {"failed"}
RUNNABLE = {"pending", "ready"}
IN_FLIGHT = {"planning", "ready", "running", "verifying"}


def _max_attempts_from_policy():
    """V0.6.6: MAX_ATTEMPTS from authoritative resource policy."""
    for candidate in (
        BASE / "bin" / "anu-resource-policy-v0.1.py",
        _HERE / "anu-resource-policy-v0.1.py",
    ):
        if not candidate.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                "anu_resource_policy_v0_1_engine",
                candidate,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return int(mod.max_attempts())
        except Exception:
            continue
    return 3


MAX_ATTEMPTS = _max_attempts_from_policy()

LOST_MUTATION_REASONS = {
    "mutation_engine_timeout",
    "invalid_mutation_engine_response",
}

# Mutation endings that fail or block a task, by failure class (V0.6.0
# section 3.4). Must match FAILURE_CLASSES in anu-recovery-v0.1.py.
FAILURE_CLASSES = {
    "ROLLED_BACK": "code_defect",
    "ABORTED_BASELINE_FAILURE": "environment",
    "MUTATION_ENGINE_ERROR": "contract",
    "ROLLBACK_VERIFICATION_FAILED": "integrity",
    "INTERRUPTED_CONFLICT": "integrity",
}

TASK_RETRY_COLUMNS = ("failure_class", "retry_after")
UNCHANGED = object()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def event(task_id, event_type, message, metadata=None):
    metadata = metadata or {}
    attempt = metadata.get("attempt")
    run_id = metadata.get("run_id")

    record = {
        "timestamp": now_iso(),
        "agent": "ANU",
        "task_id": task_id,
        "event_type": event_type,
        "message": message,
        "metadata": metadata,
    }

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

    try:
        helper = _anu_db()
        with helper.db_connect(DB_FILE) as db:
            goal_id = None
            parent_task_id = None
            if task_id is not None:
                row = db.execute(
                    "SELECT goal_id, parent_task_id FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if row:
                    goal_id, parent_task_id = row[0], row[1]

            db.execute(
                """INSERT INTO agent_events
                   (task_id, goal_id, parent_task_id, run_id, attempt,
                    event_type, message, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    goal_id,
                    parent_task_id,
                    run_id,
                    attempt,
                    event_type,
                    message,
                    json.dumps(metadata, sort_keys=True),
                    now_iso(),
                ),
            )
            db.commit()
    except Exception as exc:
        try:
            _anu_db().note_dropped_event(exc, where="task_engine.event")
        except Exception:
            pass


def db_connect():
    return _anu_db().db_connect(DB_FILE)


def dependency_admission(task_id):
    """Return Task Graph admission state without mutating task attempts."""
    if not TASK_GRAPH.is_file():
        raise RuntimeError(f"Task Graph unavailable: {TASK_GRAPH}")

    spec = importlib.util.spec_from_file_location(
        "anu_task_graph_v0_1_runtime",
        TASK_GRAPH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Task Graph module could not be loaded")

    graph = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(graph)

    db = graph.db_connect(str(DB_FILE))
    try:
        return graph.dependency_readiness(db, task_id)
    finally:
        db.close()


def enforce_dependency_admission(task_id):
    """Gate execution on Task Graph readiness before attempts or mutation."""
    admission = dependency_admission(task_id)
    state = admission.get("state")

    if state == "READY":
        return admission

    if state == "WAITING":
        event(
            task_id,
            "TASK_DEPENDENCY_WAITING",
            "task waiting for prerequisites",
            {
                "dependency_state": admission,
            },
        )
        return admission

    if state == "BLOCKED":
        update_task(task_id, status="blocked")
        event(
            task_id,
            "TASK_BLOCKED",
            "task blocked by prerequisite dependency",
            {
                "dependency_state": admission,
            },
        )
        return admission

    raise RuntimeError(
        f"Unknown Task Graph admission state: {state!r}"
    )


def fetch_task(task_id):
    with db_connect() as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            """SELECT id, goal_id, parent_task_id, task_key, title, description,
                      status, priority, attempts, created_at, updated_at
               FROM tasks
               WHERE id = ?""",
            (task_id,),
        ).fetchone()

    return dict(row) if row else None


def ensure_task_columns(db):
    present = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
    for column in TASK_RETRY_COLUMNS:
        if column not in present:
            try:
                db.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc):
                    raise


def fetch_retry_state(task_id):
    with db_connect() as db:
        ensure_task_columns(db)
        row = db.execute(
            "SELECT failure_class, retry_after FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    return {"failure_class": row[0], "retry_after": row[1]} if row else None


def update_task(
    task_id,
    status=None,
    attempts=None,
    failure_class=UNCHANGED,
    retry_after=UNCHANGED,
):
    fields = []
    values = []

    if status is not None:
        fields.append("status = ?")
        values.append(status)

    if attempts is not None:
        fields.append("attempts = ?")
        values.append(attempts)

    retry_fields = {
        "failure_class": failure_class,
        "retry_after": retry_after,
    }
    for column, value in retry_fields.items():
        if value is not UNCHANGED:
            fields.append(f"{column} = ?")
            values.append(value)

    fields.append("updated_at = ?")
    values.append(now_iso())
    values.append(task_id)

    with db_connect() as db:
        if any(v is not UNCHANGED for v in retry_fields.values()):
            ensure_task_columns(db)
        db.execute(
            f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        db.commit()


def create_task(
    title,
    description,
    priority,
    task_key=None,
    goal_id=None,
    parent_task_id=None,
):
    task_key = task_key or f"TASK-{uuid.uuid4().hex[:10].upper()}"
    created = now_iso()

    with db_connect() as db:
        existing = db.execute(
            "SELECT id FROM tasks WHERE task_key = ?",
            (task_key,),
        ).fetchone()

        if existing:
            raise RuntimeError(f"Task key already exists: {task_key}")

        cur = db.execute(
            """INSERT INTO tasks(
                   goal_id, parent_task_id, task_key, title, description,
                   status, priority, attempts, created_at, updated_at
               )
               VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?)""",
            (
                goal_id,
                parent_task_id,
                task_key,
                title,
                description,
                priority,
                created,
                created,
            ),
        )

        task_id = cur.lastrowid
        db.commit()

    event(
        task_id,
        "TASK_CREATED",
        f"{task_key}: pending",
        {
            "task_key": task_key,
            "title": title,
            "priority": priority,
            "goal_id": goal_id,
            "parent_task_id": parent_task_id,
        },
    )

    return fetch_task(task_id)


def parse_json_output(stdout):
    stdout = (stdout or "").strip()
    if not stdout:
        return None

    try:
        value = json.loads(stdout)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        lines = [line for line in stdout.splitlines() if line.strip()]
        for i in range(len(lines)):
            try:
                value = json.loads("\n".join(lines[i:]))
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                continue

    return None


def classify_mutation(result, process_code):
    status = (result or {}).get("status")

    if process_code == 0 and status in {"COMMITTED", "ALREADY_COMMITTED"}:
        return "completed"

    if status == "ROLLED_BACK":
        return "failed"

    return "blocked"


def _load_verification():
    for candidate in (
        VERIFICATION,
        _HERE / "anu-verification-v0.1.py",
    ):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "anu_verification_v0_1_engine",
                candidate,
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def _verify_mutation_attempt(task_id, attempt, run_id, result):
    """Persist verification record; gate COMPLETE on ACCEPT (V0.6.5)."""
    verification = _load_verification()
    if verification is None:
        return None

    task = fetch_task(task_id)
    goal_id = None if task is None else task.get("goal_id")
    db = db_connect()
    try:
        verification.migrate(db)
        record = verification.evaluate_mutation_attempt(
            db,
            result,
            task_id=task_id,
            attempt=attempt,
            run_id=run_id,
            goal_id=goal_id,
            declared_files=result.get("files"),
            actual_changed_files=result.get("actual_changed_files"),
            operation=result.get("operation"),
        )
        return record
    finally:
        db.close()


@contextlib.contextmanager
def shared_execution_lock():
    """Held while a task executes so recovery never treats it as orphaned."""
    EXECUTION_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(EXECUTION_LOCK_FILE, os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        try:
            # Root-run tests and the anu-agent service share this file.
            os.fchmod(fd, 0o644)
        except PermissionError:
            pass
        fcntl.flock(fd, fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)


def _load_recovery():
    spec = importlib.util.spec_from_file_location(
        "anu_recovery_v0_1_engine",
        RECOVERY,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Recovery module could not be loaded: {RECOVERY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recover_lost_mutation(task_id, attempt, run_id, result, code):
    """Reconcile a Safe Mutation that died without reporting an outcome."""
    try:
        summary = _load_recovery().recover(
            db_path=str(DB_FILE),
            lock_path=str(EXECUTION_LOCK_FILE),
        )
    except Exception as exc:
        summary = {
            "status": "RECOVERY_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }

    task = fetch_task(task_id)

    if task["status"] in IN_FLIGHT:
        # Recovery could not run, e.g. another execution holds the lock.
        # The STARTED transaction stays for startup recovery.
        update_task(
            task_id,
            status="blocked",
            failure_class="interrupted",
            retry_after=None,
        )
        event(
            task_id,
            "TASK_BLOCKED",
            "Safe Mutation lost and recovery could not run",
            {
                "attempt": attempt,
                "run_id": run_id,
                "reason": result.get("reason"),
                "recovery": summary,
            },
        )
        task = fetch_task(task_id)

    retry_state = fetch_retry_state(task_id)

    return (
        {
            **result,
            "task_status": task["status"],
            "retryable": task["status"] == "pending",
            "failure_class": retry_state["failure_class"],
            "retry_after": retry_state["retry_after"],
            "recovery": summary,
        },
        code,
    )


def _run_mutation_once(task_id, project, changes_file, operation, attempt, run_id=None):
    with shared_execution_lock():
        result, code = _run_mutation_locked(
            task_id, project, changes_file, operation, attempt, run_id
        )

    # Recovery needs the execution lock exclusively, so it runs after release.
    if result.get("reason") in LOST_MUTATION_REASONS:
        return _recover_lost_mutation(task_id, attempt, run_id, result, code)

    return result, code


def _run_mutation_locked(task_id, project, changes_file, operation, attempt, run_id=None):
    update_task(
        task_id,
        status="planning",
        attempts=attempt,
        failure_class=None,
        retry_after=None,
    )
    event(
        task_id,
        "TASK_STATE",
        "planning",
        {"attempt": attempt, "run_id": run_id},
    )

    # v0.4 still expects an already-produced mutation plan.
    # A future planner may generate the changes file before this phase.
    update_task(task_id, status="ready")
    event(
        task_id,
        "TASK_STATE",
        "ready",
        {
            "project": project,
            "changes_file": changes_file,
            "operation": operation,
            "attempt": attempt,
            "run_id": run_id,
        },
    )

    update_task(task_id, status="running")
    event(
        task_id,
        "TASK_STATE",
        "running",
        {"attempt": attempt, "run_id": run_id},
    )

    command = [
        sys.executable,
        str(SAFE_MUTATION),
        "--project",
        project,
        "--changes-file",
        changes_file,
        "--task-id",
        str(task_id),
        "--operation",
        operation,
    ]

    try:
        child_env = os.environ.copy()
        child_env["ANU_TASK_ID"] = str(task_id)
        child_env["ANU_RUN_ID"] = str(run_id or "")
        child_env["ANU_ATTEMPT"] = str(attempt)

        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            shell=False,
            check=False,
            env=child_env,
        )
    except subprocess.TimeoutExpired as exc:
        event(
            task_id,
            "TASK_MUTATION_LOST",
            "Safe Mutation Engine timed out",
            {
                "attempt": attempt,
                "run_id": run_id,
                "timeout_seconds": 300,
                "stdout": (exc.stdout or "")[-8000:]
                if exc.stdout
                else "",
                "stderr": (exc.stderr or "")[-8000:]
                if exc.stderr
                else "",
            },
        )
        return (
            {
                "ok": False,
                "task_id": task_id,
                "task_status": "running",
                "attempt": attempt,
                "run_id": run_id,
                "retryable": False,
                "reason": "mutation_engine_timeout",
            },
            5,
        )

    result = parse_json_output(process.stdout)

    if result is None:
        event(
            task_id,
            "TASK_MUTATION_LOST",
            "Invalid Safe Mutation response",
            {
                "attempt": attempt,
                "run_id": run_id,
                "process_exit_code": process.returncode,
                "stdout": process.stdout[-12000:],
                "stderr": process.stderr[-8000:],
            },
        )
        return (
            {
                "ok": False,
                "task_id": task_id,
                "task_status": "running",
                "attempt": attempt,
                "run_id": run_id,
                "retryable": False,
                "reason": "invalid_mutation_engine_response",
            },
            6,
        )

    update_task(task_id, status="verifying")
    event(
        task_id,
        "TASK_STATE",
        "verifying",
        {
            "attempt": attempt,
            "run_id": run_id,
            "transaction_id": result.get("transaction_id"),
            "mutation_status": result.get("status"),
        },
    )

    verification_record = None
    outcome = None
    recovery_action = None
    recovery_reason = None
    retry_after_minutes = None
    try:
        verification_record = _verify_mutation_attempt(
            task_id, attempt, run_id, result
        )
    except Exception as exc:
        event(
            task_id,
            "TASK_VERIFICATION_ERROR",
            "verification service failed",
            {
                "attempt": attempt,
                "run_id": run_id,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        verification_record = None

    if verification_record is not None:
        event(
            task_id,
            "TASK_VERIFICATION",
            f"verification {verification_record.get('decision')}",
            {
                "attempt": attempt,
                "run_id": run_id,
                "verification_id": verification_record.get("id"),
                "decision": verification_record.get("decision"),
                "transaction_id": result.get("transaction_id"),
            },
        )
        verification = _load_verification()
        try:
            outcome = verification.decision_to_task_outcome(
                verification_record["decision"],
                mutation_status=result.get("status"),
                evidence=verification_record.get("evidence"),
                attempt=attempt,
                max_attempts=MAX_ATTEMPTS,
            )
        except Exception:
            # V0.8.1: never break verify path if policy helper fails.
            outcome = {
                "action": None,
                "recovery_action": None,
                "task_status": (
                    "completed"
                    if verification_record.get("decision") == "ACCEPT"
                    else "failed"
                    if verification_record.get("decision") == "RETRY"
                    else "blocked"
                ),
                "failure_class": (
                    None
                    if verification_record.get("decision") == "ACCEPT"
                    else FAILURE_CLASSES.get(result.get("status"), "integrity")
                ),
                "retryable": verification_record.get("decision") == "RETRY",
                "retry_after_minutes": None,
                "reason": "recovery policy fallback",
            }
        # COMPLETE only with ACCEPT (exit criterion).
        if outcome["task_status"] == "completed":
            gate_db = db_connect()
            try:
                verification.require_accept_for_complete(
                    gate_db, task_id, attempt=attempt
                )
            except Exception as exc:
                outcome = {
                    "action": "block",
                    "recovery_action": "block",
                    "task_status": "blocked",
                    "failure_class": "integrity",
                    "retryable": False,
                    "retry_after_minutes": None,
                    "reason": "COMPLETE refused without ACCEPT verification",
                }
                event(
                    task_id,
                    "TASK_BLOCKED",
                    "COMPLETE refused without ACCEPT verification",
                    {
                        "attempt": attempt,
                        "run_id": run_id,
                        "error": str(exc),
                    },
                )
            finally:
                gate_db.close()
        final_status = outcome["task_status"]
        failure_class = outcome.get("failure_class")
        recovery_action = outcome.get("recovery_action") or outcome.get("action")
        recovery_reason = outcome.get("reason")
        retry_after_minutes = outcome.get("retry_after_minutes")
        # V0.8.1: auditable recovery classification (non-ACCEPT).
        if recovery_action and recovery_action != "none":
            retry_after_iso = None
            if retry_after_minutes:
                retry_after_iso = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=int(retry_after_minutes))
                ).isoformat()
            event(
                task_id,
                "TASK_RECOVERY_POLICY",
                recovery_reason or f"recovery {recovery_action}",
                {
                    "attempt": attempt,
                    "run_id": run_id,
                    "action": recovery_action,
                    "reason": recovery_reason,
                    "failure_class": failure_class,
                    "retry_after": retry_after_iso,
                    "retry_after_minutes": retry_after_minutes,
                    "verification_id": verification_record.get("id"),
                    "decision": verification_record.get("decision"),
                },
            )
    else:
        # Verification module missing: keep legacy classify (pre-V0.6.5).
        final_status = classify_mutation(result, process.returncode)
        failure_class = (
            FAILURE_CLASSES["ROLLED_BACK"]
            if final_status == "failed"
            else FAILURE_CLASSES.get(result.get("status"), "integrity")
        )
        outcome = None

    if final_status == "completed":
        update_task(task_id, status="completed", retry_after=None)
        event(
            task_id,
            "TASK_COMPLETED",
            "task completed",
            {
                "attempt": attempt,
                "run_id": run_id,
                "transaction_id": result.get("transaction_id"),
                "mutation_status": result.get("status"),
                "baseline_ok": result.get("baseline_ok"),
                "mutation_test_ok": result.get("mutation_test_ok"),
                "integrity_verified": result.get("integrity_verified"),
                "verification_id": (
                    None
                    if verification_record is None
                    else verification_record.get("id")
                ),
                "verification_decision": (
                    None
                    if verification_record is None
                    else verification_record.get("decision")
                ),
            },
        )
        return (
            {
                "ok": True,
                "task_id": task_id,
                "task_status": "completed",
                "attempt": attempt,
                "run_id": run_id,
                "retryable": False,
                "mutation": result,
                "verification": verification_record,
                "recovery_action": recovery_action or "none",
                "engine_exit_code": process.returncode,
                "engine_stderr": process.stderr[-8000:],
            },
            0,
        )

    if final_status == "failed":
        retry_after_iso = None
        if retry_after_minutes:
            retry_after_iso = (
                datetime.now(timezone.utc)
                + timedelta(minutes=int(retry_after_minutes))
            ).isoformat()
        update_task(
            task_id,
            status="failed",
            failure_class=failure_class or FAILURE_CLASSES["ROLLED_BACK"],
            retry_after=retry_after_iso,
        )

        if outcome is not None and "retryable" in outcome:
            retryable = bool(outcome["retryable"])
        else:
            retryable = attempt < MAX_ATTEMPTS
        event(
            task_id,
            "TASK_FAILED",
            "task failed after safe rollback",
            {
                "attempt": attempt,
                "run_id": run_id,
                "max_attempts": MAX_ATTEMPTS,
                "retryable": retryable,
                "failure_class": failure_class or FAILURE_CLASSES["ROLLED_BACK"],
                "retry_after": retry_after_iso,
                "recovery_action": recovery_action,
                "transaction_id": result.get("transaction_id"),
                "mutation_status": result.get("status"),
                "rollback_attempted": result.get("rollback_attempted"),
                "rollback_verified": result.get("rollback_verified"),
                "integrity_verified": result.get("integrity_verified"),
                "verification_id": (
                    None
                    if verification_record is None
                    else verification_record.get("id")
                ),
                "verification_decision": (
                    None
                    if verification_record is None
                    else verification_record.get("decision")
                ),
            },
        )

        return (
            {
                "ok": False,
                "task_id": task_id,
                "task_status": "failed",
                "attempt": attempt,
                "run_id": run_id,
                "retryable": retryable,
                "remaining_attempts": max(MAX_ATTEMPTS - attempt, 0),
                "mutation": result,
                "verification": verification_record,
                "recovery_action": recovery_action,
                "retry_after": retry_after_iso,
                "engine_exit_code": process.returncode,
                "engine_stderr": process.stderr[-8000:],
            },
            process.returncode or 1,
        )

    if failure_class is None:
        failure_class = FAILURE_CLASSES.get(result.get("status"), "integrity")
    update_task(
        task_id,
        status="blocked",
        failure_class=failure_class,
        retry_after=None,
    )
    event(
        task_id,
        "TASK_BLOCKED",
        "task blocked by mutation engine",
        {
            "attempt": attempt,
            "run_id": run_id,
            "transaction_id": result.get("transaction_id"),
            "mutation_status": result.get("status"),
            "failure_class": failure_class,
            "recovery_action": recovery_action,
            "verification_id": (
                None
                if verification_record is None
                else verification_record.get("id")
            ),
            "verification_decision": (
                None
                if verification_record is None
                else verification_record.get("decision")
            ),
        },
    )

    return (
        {
            "ok": False,
            "task_id": task_id,
            "task_status": "blocked",
            "attempt": attempt,
            "run_id": run_id,
            "retryable": False,
            "mutation": result,
            "verification": verification_record,
            "recovery_action": recovery_action,
            "engine_exit_code": process.returncode,
            "engine_stderr": process.stderr[-8000:],
        },
        process.returncode or 1,
    )

def start_task(task_id, project, changes_file, operation):
    task = fetch_task(task_id)

    if not task:
        raise RuntimeError(f"Task not found: {task_id}")

    if task["status"] not in RUNNABLE:
        raise RuntimeError(
            f"Task {task_id} is not startable from status={task['status']}"
        )

    admission = enforce_dependency_admission(task_id)
    if admission["state"] != "READY":
        current = fetch_task(task_id)
        return (
            {
                "ok": False,
                "task_id": task_id,
                "task_status": current["status"],
                "attempt": int(task["attempts"]),
                "retryable": task["status"] == "failed",
                "reason": admission["reason"],
                "dependency_state": admission,
            },
            2 if admission["state"] == "WAITING" else 3,
        )

    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
    task = fetch_task(task_id)
    attempt = int(task["attempts"]) + 1

    if attempt > MAX_ATTEMPTS:
        raise RuntimeError(
            f"Task {task_id} exceeded MAX_ATTEMPTS={MAX_ATTEMPTS}"
        )

    return _run_mutation_once(
        task_id,
        project,
        changes_file,
        operation,
        attempt,
        run_id=run_id,
    )


def retry_task(task_id, project, changes_file, operation):
    task = fetch_task(task_id)

    if not task:
        raise RuntimeError(f"Task not found: {task_id}")

    if task["status"] != "failed":
        raise RuntimeError(
            f"Task {task_id} is not retryable from status={task['status']}"
        )

    attempt = int(task["attempts"]) + 1

    if attempt > MAX_ATTEMPTS:
        raise RuntimeError(
            f"Task {task_id} reached MAX_ATTEMPTS={MAX_ATTEMPTS}"
        )

    admission = enforce_dependency_admission(task_id)
    if admission["state"] != "READY":
        current = fetch_task(task_id)
        return (
            {
                "ok": False,
                "task_id": task_id,
                "task_status": current["status"],
                "attempt": int(current["attempts"]),
                "retryable": current["status"] == "failed",
                "reason": admission["reason"],
                "dependency_state": admission,
            },
            2 if admission["state"] == "WAITING" else 3,
        )

    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"

    event(
        task_id,
        "TASK_RETRY_STARTED",
        "retry started with a new mutation plan",
        {
            "previous_attempt": int(task["attempts"]),
            "attempt": attempt,
            "max_attempts": MAX_ATTEMPTS,
            "project": project,
            "changes_file": changes_file,
            "operation": operation,
            "run_id": run_id,
        },
    )

    return _run_mutation_once(
        task_id,
        project,
        changes_file,
        operation,
        attempt,
        run_id=run_id,
    )


def list_tasks(limit):
    with db_connect() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT id, task_key, title, status, priority, attempts,
                      created_at, updated_at
               FROM tasks
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]


def show_task(task_id):
    return fetch_task(task_id)


def list_events(task_id, limit):
    with db_connect() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT id, task_id, goal_id, parent_task_id, run_id, attempt,
                      event_type, message, metadata, created_at
               FROM agent_events
               WHERE task_id = ?
               ORDER BY id DESC
               LIMIT ?""",
            (task_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def main():
    parser = argparse.ArgumentParser(
        description="ANU Task Engine v0.5"
    )
    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    create = sub.add_parser("create")
    create.add_argument("--title", required=True)
    create.add_argument("--description", default="")
    create.add_argument("--priority", type=int, default=5)
    create.add_argument("--task-key")
    create.add_argument("--goal-id", type=int)
    create.add_argument("--parent-task-id", type=int)

    run = sub.add_parser("run")
    run.add_argument("--task-id", type=int, required=True)
    run.add_argument("--project", required=True)
    run.add_argument("--changes-file", required=True)
    run.add_argument(
        "--operation",
        choices=["python_test", "python_compile"],
        default="python_test",
    )

    retry = sub.add_parser("retry")
    retry.add_argument("--task-id", type=int, required=True)
    retry.add_argument("--project", required=True)
    retry.add_argument("--changes-file", required=True)
    retry.add_argument(
        "--operation",
        choices=["python_test", "python_compile"],
        default="python_test",
    )

    show = sub.add_parser("show")
    show.add_argument("--task-id", type=int, required=True)

    events = sub.add_parser("events")
    events.add_argument("--task-id", type=int, required=True)
    events.add_argument("--limit", type=int, default=50)

    ls = sub.add_parser("list")
    ls.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()

    if args.command == "create":
        result = create_task(
            args.title,
            args.description,
            args.priority,
            args.task_key,
            args.goal_id,
            args.parent_task_id,
        )
        print(json.dumps({"ok": True, "task": result}, indent=2))
        return 0

    if args.command == "show":
        result = show_task(args.task_id)

        if not result:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Task not found",
                    },
                    indent=2,
                )
            )
            return 1

        print(
            json.dumps(
                {
                    "ok": True,
                    "task": result,
                    "max_attempts": MAX_ATTEMPTS,
                    "retryable": result["status"] == "failed"
                    and result["attempts"] < MAX_ATTEMPTS,
                },
                indent=2,
            )
        )
        return 0

    if args.command == "events":
        if args.limit < 1 or args.limit > 500:
            raise RuntimeError("--limit must be between 1 and 500")
        print(json.dumps({"ok": True, "task_id": args.task_id, "events": list_events(args.task_id, args.limit)}, indent=2))
        return 0

    if args.command == "list":
        if args.limit < 1 or args.limit > 200:
            raise RuntimeError("--limit must be between 1 and 200")

        print(
            json.dumps(
                {
                    "ok": True,
                    "max_attempts": MAX_ATTEMPTS,
                    "tasks": list_tasks(args.limit),
                },
                indent=2,
            )
        )
        return 0

    if args.command == "run":
        result, code = start_task(
            args.task_id,
            args.project,
            args.changes_file,
            args.operation,
        )
        print(json.dumps(result, indent=2))
        return code

    if args.command == "retry":
        result, code = retry_task(
            args.task_id,
            args.project,
            args.changes_file,
            args.operation,
        )
        print(json.dumps(result, indent=2))
        return code

    return 1


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            json.dumps(
                {"ok": False, "error": "Interrupted"},
                indent=2,
            )
        )
        sys.exit(130)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                indent=2,
            )
        )
        sys.exit(1)
