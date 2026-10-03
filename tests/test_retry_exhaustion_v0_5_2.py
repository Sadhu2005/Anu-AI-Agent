#!/usr/bin/env python3

import json
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path


BASE = Path("/srv/anu")
DB_FILE = BASE / "brain" / "anu-brain.db"
TASK_ENGINE = BASE / "bin" / "anu-task-engine-v0.5.1.py"

PROJECT_NAME = "sandbox-test-project"
PROJECT = BASE / "workspace" / "projects" / PROJECT_NAME
APP_FILE = PROJECT / "app.py"

MAX_ATTEMPTS = 3


def run_command(args, timeout=240):
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def parse_json(stdout):
    stdout = (stdout or "").strip()

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        lines = [x for x in stdout.splitlines() if x.strip()]
        for start in range(len(lines)):
            try:
                return json.loads("\n".join(lines[start:]))
            except json.JSONDecodeError:
                pass

    raise RuntimeError("Unable to parse JSON output")


def create_task(task_key):
    code, stdout, stderr = run_command(
        [
            sys.executable,
            str(TASK_ENGINE),
            "create",
            "--task-key",
            task_key,
            "--title",
            "Retry exhaustion regression",
            "--description",
            (
                "Verify retry stops at MAX_ATTEMPTS and no fourth "
                "attempt is created"
            ),
            "--priority",
            "5",
        ]
    )

    if code != 0:
        raise RuntimeError(
            f"Task creation failed\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )

    payload = parse_json(stdout)

    if payload.get("ok") is not True:
        raise RuntimeError(f"Task creation failed: {payload}")

    task_id = (payload.get("task") or {}).get("id")

    if not isinstance(task_id, int):
        raise RuntimeError(f"Invalid task_id: {payload}")

    return task_id


def query_events(task_id):
    with sqlite3.connect(DB_FILE) as db:
        rows = db.execute(
            """
            SELECT id, task_id, run_id, attempt, event_type, message
            FROM agent_events
            WHERE task_id = ?
            ORDER BY id
            """,
            (task_id,),
        ).fetchall()

    return [
        {
            "id": row[0],
            "task_id": row[1],
            "run_id": row[2],
            "attempt": row[3],
            "event_type": row[4],
            "message": row[5],
        }
        for row in rows
    ]


def query_task(task_id):
    with sqlite3.connect(DB_FILE) as db:
        row = db.execute(
            """
            SELECT id, status, attempts
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

    if row is None:
        raise AssertionError(f"Task {task_id} not found")

    return {
        "id": row[0],
        "status": row[1],
        "attempts": row[2],
    }


def validate_attempt(events, task_id, attempt, run_id):
    relevant = [
        event
        for event in events
        if event["attempt"] == attempt
        and event["event_type"] in {
            "TASK_STATE",
            "RESOURCE_ADMISSION",
            "COMMAND_STARTED",
            "COMMAND_FINISHED",
            "MUTATION_CHECKPOINT",
            "MUTATION_TRANSACTION",
            "TASK_FAILED",
            "TASK_COMPLETED",
        }
    ]

    if not relevant:
        raise AssertionError(
            f"No relevant events found for attempt={attempt}"
        )

    mismatches = []

    for event in relevant:
        if event["task_id"] != task_id:
            mismatches.append(
                f"id={event['id']} task_id={event['task_id']}"
            )

        if event["run_id"] != run_id:
            mismatches.append(
                f"id={event['id']} run_id={event['run_id']!r}"
            )

    if mismatches:
        raise AssertionError(
            f"Context mismatch on attempt {attempt}:\n"
            + "\n".join(mismatches)
        )

    return relevant


def write_failing_plan(path, new_value):
    path.write_text(
        json.dumps(
            [
                {
                    "file": "app.py",
                    "old": "    return (a + b)",
                    "new": new_value,
                }
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_expected_failure(task_id, command_args, attempt):
    code, stdout, stderr = run_command(command_args)

    result = parse_json(stdout)

    print(f"ATTEMPT_{attempt}_RESULT")
    print(json.dumps(result, indent=2))

    assert result.get("ok") is False
    assert result.get("task_id") == task_id
    assert result.get("task_status") == "failed"
    assert result.get("attempt") == attempt
    assert result.get("engine_exit_code") != 0

    # The Task Engine process wrapper may return 0 even when the
    # task itself failed. The authoritative task result is the
    # JSON payload's engine_exit_code.

    mutation = result.get("mutation") or {}

    assert mutation.get("status") == "ROLLED_BACK"
    assert mutation.get("baseline_ok") is True
    assert mutation.get("mutation_test_ok") is False
    assert mutation.get("rollback_attempted") is True
    assert mutation.get("rollback_verified") is True
    assert mutation.get("integrity_verified") is True

    return result


def main():
    task_key = (
        "TASK-RETRY-EXHAUST-"
        + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + uuid.uuid4().hex[:6].upper()
    )

    plans = [
        Path(f"/tmp/anu-exhaust-1-{uuid.uuid4().hex}.json"),
        Path(f"/tmp/anu-exhaust-2-{uuid.uuid4().hex}.json"),
        Path(f"/tmp/anu-exhaust-3-{uuid.uuid4().hex}.json"),
    ]

    backup_file = Path(
        f"/tmp/anu-exhaust-app-{uuid.uuid4().hex}.bak"
    )

    original = APP_FILE.read_text(encoding="utf-8")
    shutil.copy2(APP_FILE, backup_file)

    # Three distinct invalid plans.
    write_failing_plan(plans[0], "    return a +")
    write_failing_plan(plans[1], "    return b +")
    write_failing_plan(plans[2], "    return (a *")

    task_id = None

    try:
        task_id = create_task(task_key)

        print(f"CREATED task_id={task_id}")
        print(f"TASK_KEY={task_key}")
        print(f"MAX_ATTEMPTS={MAX_ATTEMPTS}")

        # ---------------------------------------------------------
        # Attempt 1
        # ---------------------------------------------------------
        first = run_expected_failure(
            task_id,
            [
                sys.executable,
                str(TASK_ENGINE),
                "run",
                "--task-id",
                str(task_id),
                "--project",
                PROJECT_NAME,
                "--changes-file",
                str(plans[0]),
            ],
            1,
        )

        assert first.get("retryable") is True
        assert first.get("remaining_attempts") == 2

        first_run_id = first.get("run_id")
        assert isinstance(first_run_id, str)
        assert first_run_id.startswith("run-")

        assert APP_FILE.read_text(encoding="utf-8") == original

        print("PASS attempt 1 exhaustion semantics")
        print("  retryable=true")
        print("  remaining_attempts=2")
        print("  rollback_verified=true")

        # ---------------------------------------------------------
        # Attempt 2
        # ---------------------------------------------------------
        second = run_expected_failure(
            task_id,
            [
                sys.executable,
                str(TASK_ENGINE),
                "retry",
                "--task-id",
                str(task_id),
                "--project",
                PROJECT_NAME,
                "--changes-file",
                str(plans[1]),
            ],
            2,
        )

        assert second.get("retryable") is True
        assert second.get("remaining_attempts") == 1

        second_run_id = second.get("run_id")
        assert isinstance(second_run_id, str)
        assert second_run_id.startswith("run-")
        assert second_run_id != first_run_id

        assert APP_FILE.read_text(encoding="utf-8") == original

        print("PASS attempt 2 exhaustion semantics")
        print("  retryable=true")
        print("  remaining_attempts=1")
        print("  rollback_verified=true")

        # ---------------------------------------------------------
        # Attempt 3 — terminal failure
        # ---------------------------------------------------------
        third = run_expected_failure(
            task_id,
            [
                sys.executable,
                str(TASK_ENGINE),
                "retry",
                "--task-id",
                str(task_id),
                "--project",
                PROJECT_NAME,
                "--changes-file",
                str(plans[2]),
            ],
            3,
        )

        assert third.get("retryable") is False
        assert third.get("remaining_attempts") == 0

        third_run_id = third.get("run_id")
        assert isinstance(third_run_id, str)
        assert third_run_id.startswith("run-")
        assert third_run_id not in {first_run_id, second_run_id}

        assert APP_FILE.read_text(encoding="utf-8") == original

        print("PASS attempt 3 terminal semantics")
        print("  retryable=false")
        print("  remaining_attempts=0")
        print("  rollback_verified=true")

        # ---------------------------------------------------------
        # Attempt 4 — MUST NOT EXIST
        # ---------------------------------------------------------
        code, stdout, stderr = run_command(
            [
                sys.executable,
                str(TASK_ENGINE),
                "retry",
                "--task-id",
                str(task_id),
                "--project",
                PROJECT_NAME,
                "--changes-file",
                str(plans[0]),
            ]
        )

        assert code != 0, "Attempt 4 unexpectedly succeeded"

        combined = (stdout or "") + "\n" + (stderr or "")

        assert "MAX_ATTEMPTS=3" in combined, (
            "Attempt 4 was rejected, but the output did not identify "
            "MAX_ATTEMPTS=3"
        )

        print("PASS attempt 4 rejected")
        print("  no additional retry allowed")
        print("  MAX_ATTEMPTS=3 enforced")

        # ---------------------------------------------------------
        # Event verification
        # ---------------------------------------------------------
        events = query_events(task_id)

        attempt_1_events = validate_attempt(
            events, task_id, 1, first_run_id
        )
        attempt_2_events = validate_attempt(
            events, task_id, 2, second_run_id
        )
        attempt_3_events = validate_attempt(
            events, task_id, 3, third_run_id
        )

        attempt_4_events = [
            event for event in events if event["attempt"] == 4
        ]

        assert not attempt_4_events, (
            f"Attempt 4 events unexpectedly exist: {attempt_4_events}"
        )

        assert len(
            {first_run_id, second_run_id, third_run_id}
        ) == 3

        print("PASS retry exhaustion event context")
        print(f"  attempt_1_events={len(attempt_1_events)}")
        print(f"  attempt_2_events={len(attempt_2_events)}")
        print(f"  attempt_3_events={len(attempt_3_events)}")
        print("  attempt_4_events=0")
        print(f"  run_id_1={first_run_id}")
        print(f"  run_id_2={second_run_id}")
        print(f"  run_id_3={third_run_id}")

        # ---------------------------------------------------------
        # Final task state
        # ---------------------------------------------------------
        task = query_task(task_id)

        assert task["status"] == "failed"
        assert task["attempts"] == MAX_ATTEMPTS

        print("PASS terminal task state")
        print(f"  status={task['status']}")
        print(f"  attempts={task['attempts']}")
        print("  terminal=true")

        # Final project integrity.
        assert APP_FILE.read_text(encoding="utf-8") == original

        print("PASS final project restoration")

        print()
        print("RETRY_EXHAUSTION_TEST_PASS")

        return 0

    finally:
        if backup_file.is_file():
            shutil.copy2(backup_file, APP_FILE)

        for path in (*plans, backup_file):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"RETRY_EXHAUSTION_TEST_FAILED: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
