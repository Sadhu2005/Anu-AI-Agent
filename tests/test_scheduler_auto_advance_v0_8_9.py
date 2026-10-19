#!/usr/bin/env python3
"""V0.8.9: auto-advance to next READY dependent after successful completion."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
ENG = REPO / "bin" / "anu-task-engine-v0.5.3.py"
GRAPH = REPO / "bin" / "anu-task-graph-v0.1.py"
APPROVAL = REPO / "bin" / "anu-approval-service-v0.1.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_chain_db():
    root = Path(tempfile.mkdtemp())
    db_path = root / "anu-brain.db"
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
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
            failure_class TEXT,
            retry_after TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE task_dependencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            depends_on_task_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, depends_on_task_id),
            CHECK(task_id != depends_on_task_id),
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(depends_on_task_id) REFERENCES tasks(id) ON DELETE CASCADE
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
            task_id INTEGER,
            goal_id INTEGER,
            parent_task_id INTEGER,
            run_id TEXT,
            attempt INTEGER,
            event_type TEXT,
            message TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE research (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            url TEXT,
            title TEXT,
            summary TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        "INSERT INTO goals (description) VALUES ('V0.8.9 auto-advance')"
    )
    db.execute(
        "INSERT INTO tasks (id, goal_id, title, status, priority) "
        "VALUES (1, 1, 'stage-a', 'pending', 10)"
    )
    db.execute(
        "INSERT INTO tasks (id, goal_id, title, status, priority) "
        "VALUES (2, 1, 'stage-b', 'pending', 9)"
    )
    db.execute(
        "INSERT INTO task_dependencies (task_id, depends_on_task_id) "
        "VALUES (2, 1)"
    )
    db.execute(
        "INSERT INTO task_execution_specs "
        "(task_id, project, changes_file, operation) VALUES "
        "(1, 'sandbox-test-project', 'changes.json', 'research')"
    )
    db.execute(
        "INSERT INTO task_execution_specs "
        "(task_id, project, changes_file, operation) VALUES "
        "(2, 'sandbox-test-project', 'changes.json', 'github')"
    )
    db.commit()
    return root, db_path, db


def wire_engine(eng, db_path):
    eng.DB_FILE = db_path
    eng.BASE = REPO
    eng._HERE = REPO / "bin"
    eng.TASK_GRAPH = GRAPH
    eng.LOG_FILE = db_path.parent / "engine.jsonl"

    def fake_db_connect(db_path_arg=None):
        conn = sqlite3.connect(str(eng.DB_FILE))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    return fake_db_connect


def test_find_next_ready_successor():
    eng = load(ENG, "eng_089_find")
    root, db_path, db = make_chain_db()
    try:
        wire_engine(eng, db_path)
        # Prerequisite still pending → successor not READY
        assert eng.find_next_ready_successor(db, 1) is None

        db.execute("UPDATE tasks SET status = 'completed' WHERE id = 1")
        db.commit()
        assert eng.find_next_ready_successor(db, 1) == 2

        # No dependents of stage-b
        assert eng.find_next_ready_successor(db, 2) is None
        print("PASS find_next_ready_successor")
    finally:
        db.close()


def test_maybe_auto_advance_skips_and_depth_cap():
    eng = load(ENG, "eng_089_skip")
    result = {"ok": True, "task_status": "completed"}
    out = eng._maybe_auto_advance(1, result, chain_depth=eng.MAX_AUTO_ADVANCE_CHAIN)
    assert out["auto_advance"]["skipped"] is True
    assert out["auto_advance"]["reason"] == "max_chain_depth"

    incomplete = {"ok": True, "task_status": "waiting"}
    assert "auto_advance" not in eng._maybe_auto_advance(1, incomplete)
    print("PASS maybe_auto_advance skip + depth cap")


def test_auto_advance_starts_ready_successor():
    eng = load(ENG, "eng_089_adv")
    appr = load(APPROVAL, "appr_089")
    root, db_path, db = make_chain_db()
    db.close()
    fake_db_connect = wire_engine(eng, db_path)

    call_log = []

    def fake_run(argv, capture_output=True, text=True, check=False):
        joined = " ".join(str(a) for a in argv)
        call_log.append(joined)

        class P:
            returncode = 0
            stderr = ""
            stdout = json.dumps(
                {
                    "ok": True,
                    "action": "search" if "anu-research" in joined else "repo",
                    "title": "auto-advance",
                    "research_id": 42,
                }
            )

        return P()

    with mock.patch.object(eng, "db_connect", side_effect=fake_db_connect):
        with mock.patch.object(
            eng, "shared_execution_lock", return_value=contextlib.nullcontext()
        ):
            # Approve stage-a research
            conn = fake_db_connect()
            appr.migrate(conn)
            appr.request_approval(
                conn, action="research", task_id=1, reason="test"
            )
            row = conn.execute(
                "SELECT id FROM approvals WHERE task_id = 1 ORDER BY id DESC"
            ).fetchone()
            appr.approve(conn, row["id"], decided_by="tester")
            conn.execute("UPDATE tasks SET status = 'pending' WHERE id = 1")
            conn.commit()
            conn.close()

            with mock.patch("subprocess.run", side_effect=fake_run):
                payload, code = eng.start_task(
                    1, "sandbox-test-project", "changes.json", "research"
                )

    assert code == 0, (payload, code)
    assert payload["ok"] is True
    assert payload["task_status"] == "completed"
    adv = payload.get("auto_advance") or {}
    assert adv.get("advanced") is True, adv
    assert adv.get("task_id") == 2
    assert adv.get("operation") == "github"
    # Successor should have been started; without approval it parks waiting.
    nested = adv.get("result") or {}
    assert nested.get("task_id") == 2
    assert nested.get("reason") in ("approval_required", None) or nested.get(
        "task_status"
    ) in ("waiting", "completed")
    assert any("anu-research" in c for c in call_log)

    conn = fake_db_connect()
    statuses = {
        r["id"]: r["status"]
        for r in conn.execute("SELECT id, status FROM tasks ORDER BY id")
    }
    events = [
        r["event_type"]
        for r in conn.execute(
            "SELECT event_type FROM agent_events WHERE task_id = 1 "
            "ORDER BY id"
        )
    ]
    conn.close()
    assert statuses[1] == "completed"
    assert "TASK_AUTO_ADVANCE" in events
    assert statuses[2] in ("waiting", "completed", "pending", "failed")
    print("PASS auto_advance starts ready successor")


def test_auto_advance_no_successor():
    eng = load(ENG, "eng_089_none")
    root, db_path, db = make_chain_db()
    db.execute("DELETE FROM task_dependencies")
    db.execute("DELETE FROM tasks WHERE id = 2")
    db.execute("DELETE FROM task_execution_specs WHERE task_id = 2")
    db.commit()
    db.close()
    fake_db_connect = wire_engine(eng, db_path)

    with mock.patch.object(eng, "db_connect", side_effect=fake_db_connect):
        with mock.patch.object(
            eng, "shared_execution_lock", return_value=contextlib.nullcontext()
        ):
            with mock.patch.object(
                eng,
                "_run_capability_locked",
                return_value=(
                    {
                        "ok": True,
                        "task_id": 1,
                        "task_status": "completed",
                        "attempt": 1,
                        "run_id": "r",
                        "operation": "research",
                    },
                    0,
                ),
            ):
                payload, code = eng.start_task(
                    1, "sandbox-test-project", "changes.json", "research"
                )

    assert code == 0
    assert payload["auto_advance"]["advanced"] is False
    assert payload["auto_advance"]["reason"] == "no_ready_successor"
    print("PASS auto_advance no successor")


def main():
    test_find_next_ready_successor()
    test_maybe_auto_advance_skips_and_depth_cap()
    test_auto_advance_starts_ready_successor()
    test_auto_advance_no_successor()
    print("ALL_PASS test_scheduler_auto_advance_v0_8_9")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
