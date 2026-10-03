#!/usr/bin/env python3

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


BASE = Path("/srv/anu")
PROJECTS = BASE / "workspace" / "projects"
SOURCE = PROJECTS / "sandbox-test-project"
ENGINE = BASE / "bin" / "anu-task-engine-v0.5.1.py"
DB = BASE / "brain" / "anu-brain.db"


def run_engine(task_id, project, changes_file):
    return subprocess.Popen(
        [
            sys.executable,
            str(ENGINE),
            "run",
            "--task-id",
            str(task_id),
            "--project",
            project,
            "--changes-file",
            str(changes_file),
            "--operation",
            "python_test",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def parse_output(proc):
    stdout, stderr = proc.communicate(timeout=30)
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Invalid engine JSON: {exc}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    return proc.returncode, result


def main():
    project_a = PROJECTS / "contention-test-a"
    project_b = PROJECTS / "contention-test-b"

    for project in (project_a, project_b):
        shutil.rmtree(project, ignore_errors=True)
        shutil.copytree(SOURCE, project)

        test_file = project / "tests" / "test_app.py"
        text = test_file.read_text(encoding="utf-8")
        if "import time" not in text:
            text = "import time\n" + text
        if "time.sleep(2)" not in text:
            text = text.replace(
                "def test_add(self):",
                "def test_add(self):\n        time.sleep(2)",
                1,
            )
        test_file.write_text(text, encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="anu-contention-") as td:
        td = Path(td)

        plan_a = td / "a.json"
        plan_b = td / "b.json"

        plan_a.write_text(
            json.dumps(
                [{
                    "file": "app.py",
                    "old": "return (a + b)",
                    "new": "return ((a + b) + 0)",
                }],
                indent=2,
            ),
            encoding="utf-8",
        )

        plan_b.write_text(
            json.dumps(
                [{
                    "file": "app.py",
                    "old": "return (a + b)",
                    "new": "return ((a + b) + 1 - 1)",
                }],
                indent=2,
            ),
            encoding="utf-8",
        )

        def create_task(label):
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ENGINE),
                    "create",
                    "--title",
                    f"Contention {label}",
                    "--description",
                    "V0.5.2 sandbox contention isolation regression",
                    "--priority",
                    "5",
                    "--task-key",
                    f"TASK-CONTENTION-{label}-{os.getpid()}",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            if proc.returncode != 0:
                raise AssertionError(
                    f"Task creation failed:\n{proc.stdout}\n{proc.stderr}"
                )

            return json.loads(proc.stdout)["task"]["id"]

        task_a = create_task("A")
        task_b = create_task("B")

        proc_a = run_engine(
            task_a,
            "contention-test-a",
            plan_a,
        )
        proc_b = run_engine(
            task_b,
            "contention-test-b",
            plan_b,
        )

        rc_a, result_a = parse_output(proc_a)
        rc_b, result_b = parse_output(proc_b)

        print("===== CONTENTION RESULTS =====")
        print("TASK_A")
        print(json.dumps(result_a, indent=2))
        print("TASK_B")
        print(json.dumps(result_b, indent=2))

        results = {
            task_a: result_a,
            task_b: result_b,
        }

        statuses = {
            result_a.get("task_status"),
            result_b.get("task_status"),
        }

        assert statuses == {"completed", "blocked"}, results
        print("PASS exactly one task completed and one task blocked")

        completed = result_a if result_a["task_status"] == "completed" else result_b
        blocked = result_a if result_a["task_status"] == "blocked" else result_b

        assert completed["attempt"] == 1
        assert blocked["attempt"] == 1
        print("PASS both tasks remained at attempt 1")

        assert completed["run_id"] != blocked["run_id"]
        print("PASS concurrent run IDs are unique")

        completed_project = completed["mutation"]["project"]
        blocked_project = blocked["mutation"]["project"]

        assert {completed_project, blocked_project} == {
            "contention-test-a",
            "contention-test-b",
        }
        print("PASS project identity preserved")

        assert completed["mutation"]["status"] == "COMMITTED"
        print("PASS winning task committed")

        assert blocked["mutation"]["status"] == "ABORTED_BASELINE_FAILURE"
        print("PASS losing task blocked before mutation checkpoint")

        assert blocked["mutation"]["checkpoint_path"] is None
        print("PASS losing task created no checkpoint")

        # Verify event isolation.
        with sqlite3.connect(DB) as con:
            con.row_factory = sqlite3.Row

            for task_id, result in results.items():
                rows = con.execute(
                    """
                    SELECT task_id, run_id, attempt, event_type, metadata
                    FROM agent_events
                    WHERE task_id = ?
                    ORDER BY id
                    """,
                    (task_id,),
                ).fetchall()

                expected_run = result["run_id"]

                for row in rows:
                    assert row["task_id"] == task_id

                    if row["run_id"] is not None:
                        assert row["run_id"] == expected_run

                    if row["attempt"] is not None:
                        assert row["attempt"] == 1

                print(
                    f"PASS event isolation task_id={task_id} "
                    f"events={len(rows)}"
                )

        # Verify final project state.
        app_a = (project_a / "app.py").read_text(encoding="utf-8")
        app_b = (project_b / "app.py").read_text(encoding="utf-8")

        for project_name, text in (
            ("contention-test-a", app_a),
            ("contention-test-b", app_b),
        ):
            assert (
                "return (a + b)" in text
                or "return ((a + b) + 0)" in text
                or "return ((a + b) + 1 - 1)" in text
            )

        if completed_project == "contention-test-a":
            assert "return ((a + b) + 0)" in app_a
            assert "return (a + b)" in app_b
        else:
            assert "return ((a + b) + 1 - 1)" in app_b
            assert "return (a + b)" in app_a

        print("PASS winner project contains only its own mutation")
        print("PASS blocked project remained unchanged")

    shutil.rmtree(project_a, ignore_errors=True)
    shutil.rmtree(project_b, ignore_errors=True)

    print()
    print("CONCURRENT_CONTENTION_TEST_PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"CONCURRENT_CONTENTION_TEST_FAILED: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
