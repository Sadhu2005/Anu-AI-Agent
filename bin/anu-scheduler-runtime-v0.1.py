#!/usr/bin/env python3

import fcntl
import sqlite3
from pathlib import Path


BASE = Path("/srv/anu")

DEFAULT_DB_FILE = BASE / "brain" / "anu-brain.db"
DEFAULT_LOCK_FILE = BASE / "runtime" / "anu-scheduler.lock"


def _validate_max_tasks(max_tasks):
    if isinstance(max_tasks, bool) or not isinstance(max_tasks, int):
        raise TypeError("max_tasks must be a positive integer")

    if max_tasks <= 0:
        raise ValueError("max_tasks must be a positive integer")


def _validate_cycle_callback(cycle_fn):
    if not callable(cycle_fn):
        raise TypeError("cycle_fn must be callable")


def _resolve_db_path(db_path):
    if db_path is None:
        return DEFAULT_DB_FILE

    resolved = Path(db_path)

    if not str(resolved).strip():
        raise ValueError("db_path must not be empty")

    return resolved


def _resolve_lock_path(lock_path):
    if lock_path is None:
        return DEFAULT_LOCK_FILE

    resolved = Path(lock_path)

    if not str(resolved).strip():
        raise ValueError("lock_path must not be empty")

    return resolved


def run_scheduler_cycle_once(
    db_path,
    max_tasks,
    cycle_fn,
    lock_path=None,
):
    """Run exactly one scheduler cycle under an exclusive runtime lock.

    The lifecycle layer owns the database connection for the duration of one
    cycle. It never performs scheduler mutations itself; those remain inside
    the supplied cycle callback.

    A concurrent invocation does not wait for the current cycle. It returns
    ``{"status": "SKIPPED_LOCKED"}`` without opening the database or invoking
    the callback.
    """
    _validate_max_tasks(max_tasks)
    _validate_cycle_callback(cycle_fn)

    resolved_db = _resolve_db_path(db_path)
    resolved_lock = _resolve_lock_path(lock_path)

    resolved_lock.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with resolved_lock.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            return {
                "status": "SKIPPED_LOCKED",
            }

        try:
            with sqlite3.connect(str(resolved_db)) as db:
                return cycle_fn(
                    db,
                    max_tasks,
                )
        finally:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_UN,
            )


if __name__ == "__main__":
    raise SystemExit(
        "anu-scheduler-runtime-v0.1.py is a library module; "
        "invoke run_scheduler_cycle_once() explicitly"
    )
