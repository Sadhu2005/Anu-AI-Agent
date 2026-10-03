#!/usr/bin/env python3

import hashlib
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


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, timeout=240):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )


def parse_json(stdout):
    text = stdout.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        lines = [x for x in text.splitlines() if x.strip()]
        for i in range(len(lines)):
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                pass

    raise RuntimeError("Unable to parse JSON output")


def create_task():
    key = (
        "TASK-FUNC-ROLLBACK-"
        + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + uuid.uuid4().hex[:6].upper()
    )

    result = run([
        sys.executable,
        str(TASK_ENGINE),
        "create",
        "--task-key",
        key,
        "--title",
        "Functional failure rollback regression",
        "--description",
        (
            "Verify rollback after a syntactically valid mutation "
            "causes functional tests to fail"
        ),
        "--priority",
        "5",
    ])

    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    payload = parse_json(result.stdout)

    if not payload.get("ok"):
        raise RuntimeError(f"Task creation failed: {payload}")

    return payload["task"]["id"], key


def query_mutation_events(task_id):
    with sqlite3.connect(DB_FILE) as db:
        rows = db.execute(
            """
            SELECT id, task_id, run_id, attempt, event_type, message
            FROM agent_events
            WHERE task_id = ?
              AND event_type = 'MUTATION_CHECKPOINT'
            ORDER BY id
            """,
            (task_id,),
        ).fetchall()

    return rows


def main():
    original = APP_FILE.read_text(encoding="utf-8")
    original_sha = sha256(APP_FILE)

    # This is valid Python but changes the result of add().
    mutated = original.replace(
        "    return (a + b)",
        "    return (a - b)",
        1,
    )

    if mutated == original:
        raise RuntimeError("Mutation target not found")

    changes = Path(
        f"/tmp/functional-rollback-{uuid.uuid4().hex}.json"
    )

    backup = Path(
        f"/tmp/functional-rollback-{uuid.uuid4().hex}.bak"
    )

    changes.write_text(
        json.dumps(
            [{
                "file": "app.py",
                "old": "    return (a + b)",
                "new": "    return (a - b)",
            }],
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    shutil.copy2(APP_FILE, backup)

    try:
        task_id, task_key = create_task()

        print(f"CREATED task_id={task_id}")
        print(f"TASK_KEY={task_key}")

        result = run([
            sys.executable,
            str(TASK_ENGINE),
            "run",
            "--task-id",
            str(task_id),
            "--project",
            PROJECT_NAME,
            "--changes-file",
            str(changes),
        ])

        if result.returncode != 0:
            raise RuntimeError(
                f"Task Engine process failed:\n{result.stderr}"
            )

        payload = parse_json(result.stdout)

        print("FUNCTIONAL_FAILURE_RESULT")
        print(json.dumps(payload, indent=2))

        assert payload["ok"] is False
        assert payload["task_id"] == task_id
        assert payload["task_status"] == "failed"
        assert payload["attempt"] == 1
        assert payload["retryable"] is True

        mutation = payload["mutation"]

        assert mutation["ok"] is True
        assert mutation["status"] == "ROLLED_BACK"
        assert mutation["baseline_ok"] is True
        assert mutation["mutation_test_ok"] is False
        assert mutation["rollback_attempted"] is True
        assert mutation["rollback_verified"] is True
        assert mutation["integrity_verified"] is True

        details = mutation["details"]

        rollback_sha = details["rollback_sha256"]["app.py"]

        assert rollback_sha == original_sha
        assert sha256(APP_FILE) == original_sha
        assert APP_FILE.read_text(encoding="utf-8") == original

        print("PASS functional test failure detected")
        print("PASS rollback attempted")
        print("PASS rollback SHA-256 restored")
        print("PASS rollback verification succeeded")

        events = query_mutation_events(task_id)

        states = [row[5] for row in events]

        assert any("CHECKPOINT_CREATED" in x for x in states)
        assert any("MUTATION_APPLIED" in x for x in states)
        assert any("ROLLBACK_STARTED" in x for x in states)
        assert any("ROLLBACK_VERIFIED" in x for x in states)

        print("PASS mutation checkpoint rollback lifecycle")
        print()
        print("FUNCTIONAL_FAILURE_ROLLBACK_TEST_PASS")

        return 0

    finally:
        if backup.exists():
            shutil.copy2(backup, APP_FILE)

        if changes.exists():
            changes.unlink()

        if backup.exists():
            backup.unlink()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"FUNCTIONAL_FAILURE_ROLLBACK_TEST_FAILED: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
