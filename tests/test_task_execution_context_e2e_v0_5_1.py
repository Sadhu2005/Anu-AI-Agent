#!/usr/bin/env python3

import json
import os
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

EXPECTED_EVENT_TYPES = {
    "TASK_STATE",
    "RESOURCE_ADMISSION",
    "COMMAND_STARTED",
    "COMMAND_FINISHED",
    "MUTATION_CHECKPOINT",
    "MUTATION_TRANSACTION",
    "TASK_COMPLETED",
}


def run_command(args, env=None, timeout=240):
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        shell=False,
    )

    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


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
                continue

    raise RuntimeError("Unable to parse JSON output")


def create_task(task_key):
    result = run_command(
        [
            sys.executable,
            str(TASK_ENGINE),
            "create",
            "--task-key",
            task_key,
            "--title",
            "E2E execution context regression",
            "--description",
            (
                "Verify task_id run_id and attempt remain identical "
                "through Task Engine, Gateway, Sandbox, mutation checkpoints, "
                "and transaction persistence"
            ),
            "--priority",
            "5",
        ]
    )

    if result["returncode"] != 0:
        raise RuntimeError(
            "Task creation failed\n"
            f"stdout:\n{result['stdout']}\n"
            f"stderr:\n{result['stderr']}"
        )

    payload = parse_json(result["stdout"])

    if payload.get("ok") is not True:
        raise RuntimeError(f"Task creation returned failure: {payload}")

    task = payload.get("task") or {}
    task_id = task.get("id")

    if not isinstance(task_id, int):
        raise RuntimeError(f"Invalid task id in create response: {payload}")

    return task_id


def query_events(task_id):
    with sqlite3.connect(DB_FILE) as db:
        rows = db.execute(
            """
            SELECT
                id,
                task_id,
                run_id,
                attempt,
                event_type,
                message,
                metadata
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
            "metadata": row[6],
        }
        for row in rows
    ]


def validate_event_context(events, task_id, run_id, attempt):
    relevant = [
        event
        for event in events
        if event["event_type"] in EXPECTED_EVENT_TYPES
    ]

    if not relevant:
        raise AssertionError("No relevant execution events were recorded")

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

        if event["attempt"] != attempt:
            mismatches.append(
                f"id={event['id']} attempt={event['attempt']!r}"
            )

    if mismatches:
        raise AssertionError(
            "Execution context mismatch:\n" + "\n".join(mismatches)
        )

    event_types = {event["event_type"] for event in relevant}
    missing = EXPECTED_EVENT_TYPES - event_types

    if missing:
        raise AssertionError(
            f"Missing expected event types: {sorted(missing)}"
        )

    ordered_types = [event["event_type"] for event in relevant]

    print("PASS event context propagation")
    print(f"  task_id={task_id}")
    print(f"  run_id={run_id}")
    print(f"  attempt={attempt}")
    print(f"  relevant_events={len(relevant)}")
    print(f"  event_types={ordered_types}")


def main():
    task_key = (
        "TASK-E2E-CONTEXT-"
        + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + uuid.uuid4().hex[:6].upper()
    )

    changes_file = Path(
        f"/tmp/anu-e2e-context-{uuid.uuid4().hex}.json"
    )
    backup_file = Path(
        f"/tmp/anu-e2e-context-{uuid.uuid4().hex}.app.py"
    )

    original_content = APP_FILE.read_text(encoding="utf-8")
    original_mode = APP_FILE.stat().st_mode

    mutation_content = original_content.replace(
        "    return (a + b)",
        "    return ((a + b))",
        1,
    )

    if mutation_content == original_content:
        raise RuntimeError(
            "Expected test mutation target was not found in app.py"
        )

    changes_file.write_text(
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

    backup_file.write_text(
        original_content,
        encoding="utf-8",
    )
    os.chmod(backup_file, original_mode)

    task_id = None
    run_id = None

    try:
        task_id = create_task(task_key)

        print(f"CREATED task_id={task_id}")
        print(f"TASK_KEY={task_key}")

        result = run_command(
            [
                sys.executable,
                str(TASK_ENGINE),
                "run",
                "--task-id",
                str(task_id),
                "--project",
                PROJECT_NAME,
                "--changes-file",
                str(changes_file),
            ]
        )

        if result["returncode"] != 0:
            raise RuntimeError(
                "Task Engine exited non-zero\n"
                f"stdout:\n{result['stdout']}\n"
                f"stderr:\n{result['stderr']}"
            )

        payload = parse_json(result["stdout"])

        print("TASK_ENGINE_RESULT")
        print(json.dumps(payload, indent=2))

        assert payload.get("ok") is True
        assert payload.get("task_id") == task_id
        assert payload.get("task_status") == "completed"
        assert payload.get("attempt") == 1
        assert payload.get("retryable") is False
        assert payload.get("engine_exit_code") == 0

        run_id = payload.get("run_id")
        assert isinstance(run_id, str)
        assert run_id.startswith("run-")

        mutation = payload.get("mutation") or {}

        assert mutation.get("ok") is True
        assert mutation.get("status") == "COMMITTED"
        assert mutation.get("baseline_ok") is True
        assert mutation.get("mutation_test_ok") is True
        assert mutation.get("integrity_verified") is True

        print("PASS task engine lifecycle")
        print(f"  task_id={task_id}")
        print(f"  run_id={run_id}")
        print(f"  attempt=1")
        print("  task_status=completed")
        print("  mutation_status=COMMITTED")

        events = query_events(task_id)

        validate_event_context(
            events,
            task_id=task_id,
            run_id=run_id,
            attempt=1,
        )

        final_content = APP_FILE.read_text(encoding="utf-8")

        assert final_content == mutation_content, (
            "Expected committed mutation was not present in project"
        )

        print("PASS mutation applied to project")

        return 0

    finally:
        # Cleanup is intentionally outside the ANU mutation path.
        # The E2E test has already validated the real mutation lifecycle;
        # this restores the disposable test project without creating extra
        # mutation events for the task being inspected.
        try:
            if backup_file.is_file():
                shutil.copy2(backup_file, APP_FILE)
                os.chmod(APP_FILE, original_mode)
        finally:
            try:
                if changes_file.exists():
                    changes_file.unlink()
            except OSError:
                pass

            try:
                if backup_file.exists():
                    backup_file.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"E2E_REGRESSION_TEST_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
