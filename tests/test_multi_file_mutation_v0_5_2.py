#!/usr/bin/env python3

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path("/srv/anu/workspace/projects/sandbox-test-project")
APP = PROJECT_ROOT / "app.py"
TEST_APP = PROJECT_ROOT / "tests/test_app.py"

MUTATION_ENGINE = Path("/srv/anu/bin/anu-safe-mutation-v0.2.py")


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_mutation(changes_file, task_id):
    env = os.environ.copy()

    # Preserve execution-context propagation semantics used by ANU.
    env["ANU_TASK_ID"] = str(task_id)
    env["ANU_GOAL_ID"] = ""
    env["ANU_PARENT_TASK_ID"] = ""
    env["ANU_RUN_ID"] = f"multi-file-regression-{task_id}"
    env["ANU_ATTEMPT"] = "1"

    proc = subprocess.run(
        [
            sys.executable,
            str(MUTATION_ENGINE),
            "--project",
            "sandbox-test-project",
            "--changes-file",
            str(changes_file),
            "--task-id",
            str(task_id),
            "--operation",
            "python_test",
        ],
        text=True,
        capture_output=True,
        env=env,
    )

    # The mutation engine prints JSON even when the mutation result itself
    # represents a failed test/rollback.
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise AssertionError(
            f"Mutation engine did not return JSON: {exc}"
        )

    return proc.returncode, result


def main():
    original_app = APP.read_bytes()
    original_test = TEST_APP.read_bytes()

    original_app_sha = sha256_file(APP)
    original_test_sha = sha256_file(TEST_APP)

    assert b"return (a + b)" in original_app
    assert b"self.assertEqual(add(2, 3), 5)" in original_test

    with tempfile.TemporaryDirectory(prefix="anu-multi-file-") as td:
        changes_file = Path(td) / "changes.json"

        changes = [
            {
                "file": "app.py",
                "old": "return (a + b)",
                "new": "return (a - b)",
            },
            {
                "file": "tests/test_app.py",
                "old": "from app import add",
                "new": "from app import add\n\n# Multi-file mutation regression",
            },
        ]

        changes_file.write_text(
            json.dumps(changes, indent=2),
            encoding="utf-8",
        )

        returncode, result = run_mutation(
            changes_file,
            task_id=9001,
        )

        print("MULTI_FILE_RESULT")
        print(json.dumps(result, indent=2))

        assert result["status"] == "ROLLED_BACK", result
        print("PASS multi-file transaction rolled back")

        assert result["rollback_attempted"] is True
        print("PASS rollback attempted")

        assert result["rollback_verified"] is True
        print("PASS rollback test verified")

        assert result["integrity_verified"] is True
        print("PASS multi-file integrity verified")

        details = result["details"]

        rollback_sha = details["rollback_sha256"]

        assert rollback_sha["app.py"] == original_app_sha
        print("PASS app.py SHA-256 restored")

        assert rollback_sha["tests/test_app.py"] == original_test_sha
        print("PASS tests/test_app.py SHA-256 restored")

        assert APP.read_bytes() == original_app
        print("PASS app.py exact bytes restored")

        assert TEST_APP.read_bytes() == original_test
        print("PASS tests/test_app.py exact bytes restored")

        assert sha256_file(APP) == original_app_sha
        assert sha256_file(TEST_APP) == original_test_sha
        print("PASS final project hashes match baseline")

        print()
        print("MULTI_FILE_MUTATION_TEST_PASS")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"MULTI_FILE_MUTATION_TEST_FAILED: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
