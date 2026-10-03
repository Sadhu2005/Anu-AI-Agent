#!/usr/bin/env python3

import sqlite3


def migrate(db):
    """Create the task execution-spec storage.

    This migration is idempotent and does not modify the tasks table.
    Execution specifications are owned by this module and are keyed
    one-to-one by task_id.
    """
    if db is None or not hasattr(db, "executescript"):
        raise TypeError("db must provide an executescript() method")

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(task_id)
                REFERENCES tasks(id)
                ON DELETE CASCADE
        );
        """
    )

    db.commit()


def _validate_descriptor_fields(project, changes_file, operation):
    """Validate the fields required by the task execution contract."""
    fields = {
        "project": project,
        "changes_file": changes_file,
        "operation": operation,
    }

    for name, value in fields.items():
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")

        if not value.strip():
            raise ValueError(f"{name} must not be empty")

    return {
        "project": project,
        "changes_file": changes_file,
        "operation": operation,
    }


def _task_exists(db, task_id):
    row = db.execute(
        """
        SELECT id
        FROM tasks
        WHERE id = ?
        """,
        (task_id,),
    ).fetchone()

    return row is not None


def save_execution_descriptor(
    db,
    task_id,
    project,
    changes_file,
    operation,
):
    """Create or replace the execution descriptor for an existing task."""
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    descriptor = _validate_descriptor_fields(
        project,
        changes_file,
        operation,
    )

    if not isinstance(task_id, int) or isinstance(task_id, bool):
        raise TypeError("task_id must be an integer")

    if not _task_exists(db, task_id):
        raise ValueError(
            f"Task does not exist: task_id={task_id}"
        )

    row = db.execute(
        """
        SELECT task_id
        FROM task_execution_specs
        WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()

    now = _utc_now_iso()

    if row is None:
        db.execute(
            """
            INSERT INTO task_execution_specs(
                task_id,
                project,
                changes_file,
                operation,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                descriptor["project"],
                descriptor["changes_file"],
                descriptor["operation"],
                now,
                now,
            ),
        )
    else:
        db.execute(
            """
            UPDATE task_execution_specs
            SET
                project = ?,
                changes_file = ?,
                operation = ?,
                updated_at = ?
            WHERE task_id = ?
            """,
            (
                descriptor["project"],
                descriptor["changes_file"],
                descriptor["operation"],
                now,
                task_id,
            ),
        )

    db.commit()

    return {
        "task_id": task_id,
        **descriptor,
    }


def fetch_execution_descriptor(db, task_id):
    """Fetch the execution descriptor for a task or return None."""
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")

    if not isinstance(task_id, int) or isinstance(task_id, bool):
        raise TypeError("task_id must be an integer")

    row = db.execute(
        """
        SELECT
            task_id,
            project,
            changes_file,
            operation
        FROM task_execution_specs
        WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()

    if row is None:
        return None

    if hasattr(row, "keys"):
        return {
            "task_id": row["task_id"],
            "project": row["project"],
            "changes_file": row["changes_file"],
            "operation": row["operation"],
        }

    return {
        "task_id": row[0],
        "project": row[1],
        "changes_file": row[2],
        "operation": row[3],
    }


def _utc_now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
