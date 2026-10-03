#!/usr/bin/env python3

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


BASE = Path("/srv/anu")
REAL_DB = BASE / "brain" / "anu-brain.db"
ENGINE_PATH = BASE / "bin" / "anu-task-engine-v0.5.3.py"
GRAPH_PATH = BASE / "bin" / "anu-task-graph-v0.1.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_database(source, destination):
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)

    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def make_task(db, key, status="pending", attempts=0):
    cur = db.execute(
        """
        INSERT INTO tasks(
            goal_id,
            parent_task_id,
            task_key,
            title,
            description,
            status,
            priority,
            attempts,
            created_at,
            updated_at
        )
        VALUES (
            NULL,
            NULL,
            ?,
            ?,
            '',
            ?,
            5,
            ?,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        """,
        (key, key, status, attempts),
    )
    return cur.lastrowid


def main():
    print("===== ANU V0.5.3 TASK ENGINE DEPENDENCY ADMISSION =====")

    assert REAL_DB.is_file()
    assert ENGINE_PATH.is_file()
    assert GRAPH_PATH.is_file()

    graph = load_module(
        "anu_task_graph_v0_1_test",
        GRAPH_PATH,
    )

    engine = load_module(
        "anu_task_engine_v0_5_3_test",
        ENGINE_PATH,
    )

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="anu-v053-dependency-regression-"
        )
    )

    temp_db = temp_dir / "anu-brain.db"
    temp_log = temp_dir / "anu-task-engine.jsonl"

    copy_database(REAL_DB, temp_db)

    engine.DB_FILE = temp_db
    engine.LOG_FILE = temp_log

    db = graph.db_connect(str(temp_db))

    try:
        graph.migrate(db)

        assert db.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0] == 1

        assert len(
            db.execute(
                "PRAGMA foreign_key_check(task_dependencies)"
            ).fetchall()
        ) == 0

        waiting_prereq = make_task(
            db,
            "V053-REG-PREREQ-PENDING",
        )
        waiting_task = make_task(
            db,
            "V053-REG-WAITING",
        )

        retry_prereq = make_task(
            db,
            "V053-REG-PREREQ-RETRYABLE",
            status="failed",
            attempts=1,
        )
        retry_task = make_task(
            db,
            "V053-REG-RETRY-WAITING",
            status="failed",
            attempts=1,
        )

        blocked_prereq = make_task(
            db,
            "V053-REG-PREREQ-BLOCKED",
            status="blocked",
        )
        blocked_task = make_task(
            db,
            "V053-REG-BLOCKED",
        )

        exhausted_prereq = make_task(
            db,
            "V053-REG-PREREQ-EXHAUSTED",
            status="failed",
            attempts=3,
        )
        exhausted_task = make_task(
            db,
            "V053-REG-EXHAUSTED",
        )

        completed_prereq = make_task(
            db,
            "V053-REG-PREREQ-COMPLETED",
            status="completed",
            attempts=1,
        )
        ready_task = make_task(
            db,
            "V053-REG-READY",
        )

        db.commit()

        graph.add_dependency(
            db,
            waiting_task,
            waiting_prereq,
        )
        graph.add_dependency(
            db,
            retry_task,
            retry_prereq,
        )
        graph.add_dependency(
            db,
            blocked_task,
            blocked_prereq,
        )
        graph.add_dependency(
            db,
            exhausted_task,
            exhausted_prereq,
        )
        graph.add_dependency(
            db,
            ready_task,
            completed_prereq,
        )

        def mutation_must_not_run(*args, **kwargs):
            raise AssertionError(
                "Safe Mutation was reached by dependency-blocked task"
            )

        engine._run_mutation_once = mutation_must_not_run

        result, code = engine.start_task(
            waiting_task,
            "sandbox-test-project",
            "/tmp/unused.json",
            "python_test",
        )

        assert code == 2
        assert result["reason"] == "PREREQUISITES_INCOMPLETE"

        row = db.execute(
            "SELECT status, attempts FROM tasks WHERE id = ?",
            (waiting_task,),
        ).fetchone()

        assert tuple(row) == ("pending", 0)

        print("PASS start WAITING gate")

        result, code = engine.retry_task(
            retry_task,
            "sandbox-test-project",
            "/tmp/unused.json",
            "python_test",
        )

        assert code == 2
        assert result["reason"] == "PREREQUISITES_INCOMPLETE"

        row = db.execute(
            "SELECT status, attempts FROM tasks WHERE id = ?",
            (retry_task,),
        ).fetchone()

        assert tuple(row) == ("failed", 1)

        print("PASS retry WAITING gate")

        result, code = engine.start_task(
            blocked_task,
            "sandbox-test-project",
            "/tmp/unused.json",
            "python_test",
        )

        assert code == 3
        assert result["reason"] == "PREREQUISITE_TERMINALLY_UNAVAILABLE"

        row = db.execute(
            "SELECT status, attempts FROM tasks WHERE id = ?",
            (blocked_task,),
        ).fetchone()

        assert tuple(row) == ("blocked", 0)

        print("PASS BLOCKED gate")

        readiness = graph.dependency_readiness(
            db,
            retry_task,
        )

        assert readiness["state"] == "WAITING"
        assert readiness["reason"] == "PREREQUISITES_INCOMPLETE"

        print("PASS retryable failed prerequisite")

        readiness = graph.dependency_readiness(
            db,
            exhausted_task,
        )

        assert readiness["state"] == "BLOCKED"
        assert readiness["reason"] == (
            "PREREQUISITE_TERMINALLY_UNAVAILABLE"
        )

        result, code = engine.start_task(
            exhausted_task,
            "sandbox-test-project",
            "/tmp/unused.json",
            "python_test",
        )

        assert code == 3

        row = db.execute(
            "SELECT status, attempts FROM tasks WHERE id = ?",
            (exhausted_task,),
        ).fetchone()

        assert tuple(row) == ("blocked", 0)

        print("PASS exhausted prerequisite")

        readiness = graph.dependency_readiness(
            db,
            ready_task,
        )

        assert readiness["state"] == "READY"
        assert readiness["reason"] == "ALL_DEPENDENCIES_COMPLETED"

        captured = {}

        def mutation_sentinel(
            task_id,
            project,
            changes_file,
            operation,
            attempt,
            run_id=None,
        ):
            captured.update(
                {
                    "task_id": task_id,
                    "project": project,
                    "changes_file": changes_file,
                    "operation": operation,
                    "attempt": attempt,
                    "run_id": run_id,
                }
            )

            return (
                {
                    "sentinel": True,
                    "task_id": task_id,
                    "attempt": attempt,
                    "run_id": run_id,
                },
                0,
            )

        engine._run_mutation_once = mutation_sentinel

        result, code = engine.start_task(
            ready_task,
            "sandbox-test-project",
            "/tmp/ready.json",
            "python_test",
        )

        assert code == 0
        assert captured["task_id"] == ready_task
        assert captured["attempt"] == 1
        assert captured["run_id"]

        print("PASS completed prerequisite → READY")

        violations = db.execute(
            "PRAGMA foreign_key_check(task_dependencies)"
        ).fetchall()

        assert len(violations) == 0

        assert db.execute(
            "SELECT COUNT(*) FROM task_dependencies"
        ).fetchone()[0] == 5

    finally:
        db.close()

    print("TASK_ENGINE_DEPENDENCY_ADMISSION_REGRESSION_PASS=1")
    print("REAL_DATABASE_TOUCHED=0")
    print("REAL_MUTATION_EXECUTED=0")


if __name__ == "__main__":
    main()
