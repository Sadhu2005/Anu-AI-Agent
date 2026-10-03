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
            "Retry lifecycle regression",
            "--description",
            (
                "Verify failed mutation becomes retryable and a new "
                "mutation plan succeeds on the next attempt"
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


def main():
    task_key = (
        "TASK-RETRY-"
        + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + uuid.uuid4().hex[:6].upper()
    )

    failing_plan = Path(
        f"/tmp/anu-retry-failing-{uuid.uuid4().hex}.json"
    )

    successful_plan = Path(
        f"/tmp/anu-retry-success-{uuid.uuid4().hex}.json"
    )

    backup_file = Path(
        f"/tmp/anu-retry-app-{uuid.uuid4().hex}.bak"
    )

    original = APP_FILE.read_text(encoding="utf-8")
    shutil.copy2(APP_FILE, backup_file)

    # Attempt 1: syntactically invalid mutation.
    failing_plan.write_text(
        json.dumps(
            [
                {
                    "file": "app.py",
                    "old": "    return (a + b)",
                    "new": "    return a +",
                }
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Attempt 2: deliberately different, valid mutation plan.
    successful_plan.write_text(
        json.dumps(
            [
                {
                    "file": "app.py",
                    "old": "    return (a + b)",
                    "new": "    return ((a + b))",
                }
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    task_id = None

    try:
        task_id = create_task(task_key)

        print(f"CREATED task_id={task_id}")
        print(f"TASK_KEY={task_key}")

        # -----------------------------
        # Attempt 1: expected failure
        # -----------------------------
        code, stdout, stderr = run_command(
            [
                sys.executable,
                str(TASK_ENGINE),
                "run",
                "--task-id",
                str(task_id),
                "--project",
                PROJECT_NAME,
                "--changes-file",
                str(failing_plan),
            ]
        )

        if code != 0:
            raise RuntimeError(
                "Task Engine process failed unexpectedly on attempt 1\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr}"
            )

        first = parse_json(stdout)

        print("ATTEMPT_1_RESULT")
        print(json.dumps(first, indent=2))

        assert first.get("ok") is False
        assert first.get("task_id") == task_id
        assert first.get("task_status") == "failed"
        assert first.get("attempt") == 1
        assert first.get("retryable") is True
        assert first.get("engine_exit_code") != 0

        first_run_id = first.get("run_id")

        assert isinstance(first_run_id, str)
        assert first_run_id.startswith("run-")

        mutation_1 = first.get("mutation") or {}

        assert mutation_1.get("status") == "ROLLED_BACK"

        print("PASS attempt 1 failure semantics")
        print(f"  task_id={task_id}")
        print(f"  run_id={first_run_id}")
        print("  attempt=1")
        print("  task_status=failed")
        print("  retryable=true")
        print("  mutation_status=ROLLED_BACK")

        # Safe Mutation should have restored the source automatically.
        after_failure = APP_FILE.read_text(encoding="utf-8")

        assert after_failure == original, (
            "Project was not restored after failed mutation"
        )

        print("PASS automatic rollback after failed mutation")

        # -----------------------------
        # Attempt 2: expected success
        # -----------------------------
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
                str(successful_plan),
            ]
        )

        if code != 0:
            raise RuntimeError(
                "Task Engine process failed unexpectedly on retry\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr}"
            )

        second = parse_json(stdout)

        print("ATTEMPT_2_RESULT")
        print(json.dumps(second, indent=2))

        assert second.get("ok") is True
        assert second.get("task_id") == task_id
        assert second.get("task_status") == "completed"
        assert second.get("attempt") == 2
        assert second.get("retryable") is False
        assert second.get("engine_exit_code") == 0

        second_run_id = second.get("run_id")

        assert isinstance(second_run_id, str)
        assert second_run_id.startswith("run-")
        assert second_run_id != first_run_id

        mutation_2 = second.get("mutation") or {}

        assert mutation_2.get("status") == "COMMITTED"
        assert mutation_2.get("baseline_ok") is True
        assert mutation_2.get("mutation_test_ok") is True
        assert mutation_2.get("integrity_verified") is True

        print("PASS attempt 2 retry semantics")
        print(f"  task_id={task_id}")
        print(f"  run_id={second_run_id}")
        print("  attempt=2")
        print("  task_status=completed")
        print("  retryable=false")
        print("  mutation_status=COMMITTED")

        # -----------------------------
        # Event context verification
        # -----------------------------
        events = query_events(task_id)

        attempt_1_events = validate_attempt(
            events,
            task_id,
            1,
            first_run_id,
        )

        attempt_2_events = validate_attempt(
            events,
            task_id,
            2,
            second_run_id,
        )

        print("PASS retry event context propagation")
        print(f"  attempt_1_events={len(attempt_1_events)}")
        print(f"  attempt_2_events={len(attempt_2_events)}")
        print(f"  run_id_1={first_run_id}")
        print(f"  run_id_2={second_run_id}")

        # Verify the second mutation actually reached the project.
        expected_retry_content = original.replace(
            "    return (a + b)",
            "    return ((a + b))",
            1,
        )

        assert APP_FILE.read_text(encoding="utf-8") == expected_retry_content

        print("PASS retry mutation applied")

        print()
        print("RETRY_REGRESSION_TEST_PASS")

        return 0

    finally:
        # Always restore the disposable project source.
        if backup_file.is_file():
            shutil.copy2(backup_file, APP_FILE)

        for path in (
            failing_plan,
            successful_plan,
            backup_file,
        ):
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
            f"RETRY_REGRESSION_TEST_FAILED: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
