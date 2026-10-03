#!/usr/bin/env python3

import fcntl
import importlib.util
import sqlite3
import tempfile
from pathlib import Path


BASE = Path("/srv/anu")


def load_lifecycle():
    path = BASE / "bin" / "anu-scheduler-runtime-v0.1.py"

    spec = importlib.util.spec_from_file_location(
        "anu_scheduler_runtime_v0_1_lifecycle_test",
        path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load lifecycle module: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_db(path):
    db = sqlite3.connect(path)

    db.execute(
        "CREATE TABLE marker (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
    )

    db.execute(
        "INSERT INTO marker(id, value) VALUES (?, ?)",
        (1, "initial"),
    )

    db.commit()
    db.close()


def read_marker(path):
    db = sqlite3.connect(path)

    rows = db.execute(
        "SELECT id, value FROM marker ORDER BY id"
    ).fetchall()

    db.close()
    return rows


def test_single_cycle_invokes_callback_once():
    lifecycle = load_lifecycle()

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        lock_path = Path(td) / "scheduler.lock"

        build_db(db_path)

        calls = []

        def cycle_fn(db, max_tasks):
            calls.append(max_tasks)

            row = db.execute(
                "SELECT value FROM marker WHERE id = 1"
            ).fetchone()

            assert row == ("initial",)

            return {
                "status": "COMPLETED",
                "max_tasks": max_tasks,
            }

        result = lifecycle.run_scheduler_cycle_once(
            str(db_path),
            3,
            cycle_fn,
            lock_path=str(lock_path),
        )

        assert result == {
            "status": "COMPLETED",
            "max_tasks": 3,
        }

        assert calls == [3]

        print("PASS single lifecycle cycle invokes callback once")


def test_invalid_max_tasks_rejected_without_callback():
    lifecycle = load_lifecycle()

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        lock_path = Path(td) / "scheduler.lock"

        build_db(db_path)
        calls = []

        def cycle_fn(db, max_tasks):
            calls.append(max_tasks)
            return {"status": "COMPLETED"}

        for invalid in (0, -1, True, 1.5, "3", None):
            try:
                lifecycle.run_scheduler_cycle_once(
                    str(db_path),
                    invalid,
                    cycle_fn,
                    lock_path=str(lock_path),
                )
            except (TypeError, ValueError):
                continue

            raise AssertionError(
                f"Expected max_tasks={invalid!r} to be rejected"
            )

        assert calls == []

        print("PASS lifecycle validates max_tasks")


def test_cycle_receives_database_connection():
    lifecycle = load_lifecycle()

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        lock_path = Path(td) / "scheduler.lock"

        build_db(db_path)

        observed = {}

        def cycle_fn(db, max_tasks):
            observed["connection_type"] = type(db).__name__
            observed["query"] = db.execute(
                "SELECT value FROM marker WHERE id = 1"
            ).fetchone()
            observed["max_tasks"] = max_tasks
            return "OK"

        result = lifecycle.run_scheduler_cycle_once(
            str(db_path),
            2,
            cycle_fn,
            lock_path=str(lock_path),
        )

        assert result == "OK"

        assert observed == {
            "connection_type": "Connection",
            "query": ("initial",),
            "max_tasks": 2,
        }

        print("PASS lifecycle owns cycle database connection")


def test_lock_contention_skips_without_callback():
    lifecycle = load_lifecycle()

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        lock_path = Path(td) / "scheduler.lock"

        build_db(db_path)

        holder = open(lock_path, "a+")

        try:
            fcntl.flock(
                holder.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )

            calls = []

            def cycle_fn(db, max_tasks):
                calls.append(max_tasks)
                return "SHOULD_NOT_RUN"

            result = lifecycle.run_scheduler_cycle_once(
                str(db_path),
                5,
                cycle_fn,
                lock_path=str(lock_path),
            )

            assert result == {
                "status": "SKIPPED_LOCKED",
            }

            assert calls == []

            print("PASS lock contention skips cycle without callback")

        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()


def test_lock_released_after_cycle_exception():
    lifecycle = load_lifecycle()

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        lock_path = Path(td) / "scheduler.lock"

        build_db(db_path)

        def failing_cycle(db, max_tasks):
            raise RuntimeError("intentional cycle failure")

        try:
            lifecycle.run_scheduler_cycle_once(
                str(db_path),
                1,
                failing_cycle,
                lock_path=str(lock_path),
            )
        except RuntimeError as exc:
            assert str(exc) == "intentional cycle failure"
        else:
            raise AssertionError(
                "Expected cycle failure to propagate"
            )

        calls = []

        def recovery_cycle(db, max_tasks):
            calls.append(max_tasks)
            return "RECOVERED"

        result = lifecycle.run_scheduler_cycle_once(
            str(db_path),
            1,
            recovery_cycle,
            lock_path=str(lock_path),
        )

        assert result == "RECOVERED"
        assert calls == [1]

        print("PASS lifecycle releases lock after exception")


def test_lifecycle_does_not_change_db_state_itself():
    lifecycle = load_lifecycle()

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test.db"
        lock_path = Path(td) / "scheduler.lock"

        build_db(db_path)
        before = read_marker(db_path)

        def read_only_cycle(db, max_tasks):
            return db.execute(
                "SELECT COUNT(*) FROM marker"
            ).fetchone()[0]

        result = lifecycle.run_scheduler_cycle_once(
            str(db_path),
            1,
            read_only_cycle,
            lock_path=str(lock_path),
        )

        after = read_marker(db_path)

        assert result == 1
        assert before == after

        print("PASS lifecycle does not mutate DB itself")


def main():
    print("===== ANU V0.5.4 SCHEDULER LIFECYCLE REGRESSION =====")

    test_single_cycle_invokes_callback_once()
    test_invalid_max_tasks_rejected_without_callback()
    test_cycle_receives_database_connection()
    test_lock_contention_skips_without_callback()
    test_lock_released_after_cycle_exception()
    test_lifecycle_does_not_change_db_state_itself()

    print("SCHEDULER_LIFECYCLE_TEST_COMPLETE")


if __name__ == "__main__":
    main()
