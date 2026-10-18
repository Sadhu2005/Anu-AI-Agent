#!/usr/bin/env python3
"""V0.7.0: goal cycle expands pending goals and syncs completion."""

import importlib.util
import os
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-goal-cycle-v0.1.py"


def load_cycle():
    spec = importlib.util.spec_from_file_location("goal_cycle_v070", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BASE = REPO
    return mod


def make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            repository TEXT NOT NULL,
            workspace TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id)
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            parent_task_id INTEGER,
            task_key TEXT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 5,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(goal_id) REFERENCES goals(id)
        );
        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, depends_on_task_id),
            CHECK(task_id != depends_on_task_id)
        );
        CREATE TABLE task_execution_specs (
            task_id INTEGER PRIMARY KEY,
            project TEXT NOT NULL,
            changes_file TEXT NOT NULL,
            operation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            task_id INTEGER,
            goal_id INTEGER,
            parent_task_id INTEGER,
            run_id TEXT,
            attempt INTEGER
        );
        """
    )
    db.execute(
        """INSERT INTO projects(project_key, name, repository, workspace)
           VALUES ('sandbox-test-project', 'Sandbox', 'local',
                   'workspace/projects/sandbox-test-project')"""
    )
    db.execute(
        """INSERT INTO goals(project_id, description, status)
           VALUES (1, 'Improve sandbox', 'pending')"""
    )
    db.commit()
    return path, db


def test_expand_then_sync_completes():
    cycle = load_cycle()
    path, db = make_db()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    try:
        expand = cycle.expand_pending_goals(db, max_goals=1)
        assert expand["expanded_count"] == 1, expand
        assert expand["expanded"][0]["goal_id"] == 1
        assert expand["expanded"][0]["task_count"] == 6

        goal = db.execute("SELECT status FROM goals WHERE id = 1").fetchone()
        assert goal["status"] == "active"

        # Idempotent: already expanded even if status forced back to pending.
        assert cycle.goal_already_expanded(db, 1) is True
        db.execute("UPDATE goals SET status = 'pending' WHERE id = 1")
        db.commit()
        again = cycle.expand_pending_goals(db, max_goals=1)
        assert again["expanded_count"] == 0, again
        assert any(s["reason"] == "already_expanded" for s in again["skipped"])

        db.execute("UPDATE tasks SET status = 'completed' WHERE goal_id = 1")
        db.commit()

        # V0.6.5+: goal COMPLETE requires per-task ACCEPT verification.
        vspec = importlib.util.spec_from_file_location(
            "verification_v070",
            REPO / "bin" / "anu-verification-v0.1.py",
        )
        vmod = importlib.util.module_from_spec(vspec)
        vspec.loader.exec_module(vmod)
        vmod.migrate(db)
        for row in db.execute(
            "SELECT id FROM tasks WHERE goal_id = 1 ORDER BY id"
        ).fetchall():
            vmod.persist_record(
                db,
                task_id=row["id"],
                goal_id=1,
                attempt=1,
                decision=vmod.DECISION_ACCEPT,
                evidence={"seeded": True},
            )

        sync = cycle.sync_goal_statuses(db)
        assert sync["changed"] >= 1, sync
        goal = db.execute("SELECT status FROM goals WHERE id = 1").fetchone()
        assert goal["status"] == "completed", dict(goal)
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
        db.close()
        path.unlink(missing_ok=True)
    print("PASS expand -> complete tasks -> sync completed")


def test_skips_goal_without_project():
    cycle = load_cycle()
    path, db = make_db()
    db.execute(
        "INSERT INTO goals(description, status) VALUES ('orphan', 'pending')"
    )
    db.commit()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    try:
        # Expand only the orphan (id=2) by completing project goal first path:
        # max_goals=1 takes oldest pending (id=1 with project). Expand id=1 then
        # check orphan alone.
        cycle.expand_pending_goals(db, max_goals=1)
        result = cycle.expand_pending_goals(db, max_goals=1)
        assert result["expanded_count"] == 0
        reasons = [s["reason"] for s in result["skipped"]]
        assert "no_project" in reasons or "already_expanded" in reasons
        # Force orphan-only: mark goal 1 completed so only orphan remains pending.
        db.execute("UPDATE goals SET status = 'completed' WHERE id = 1")
        db.commit()
        orphan = cycle.expand_pending_goals(db, max_goals=1)
        assert orphan["expanded_count"] == 0
        assert any(s["reason"] == "no_project" for s in orphan["skipped"])
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
        db.close()
        path.unlink(missing_ok=True)
    print("PASS skip pending goal without project")


def test_fifo_next_goal():
    cycle = load_cycle()
    path, db = make_db()
    db.execute(
        """INSERT INTO goals(project_id, description, status)
           VALUES (1, 'Second', 'pending')"""
    )
    db.commit()
    old = os.environ.get("ANU_BASE")
    os.environ["ANU_BASE"] = str(REPO)
    try:
        first = cycle.expand_pending_goals(db, max_goals=1)
        assert first["expanded"][0]["goal_id"] == 1
        second = cycle.expand_pending_goals(db, max_goals=1)
        assert second["expanded"][0]["goal_id"] == 2
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
        db.close()
        path.unlink(missing_ok=True)
    print("PASS FIFO next-goal selection")


if __name__ == "__main__":
    print("===== ANU V0.7.0 GOAL CYCLE =====")
    test_expand_then_sync_completes()
    test_skips_goal_without_project()
    test_fifo_next_goal()
    print("GOAL_CYCLE_TEST_COMPLETE")
