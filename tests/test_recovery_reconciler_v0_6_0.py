#!/usr/bin/env python3

import fcntl
import importlib.util
import json
import os
import sqlite3
import stat
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SAFE_MUTATION = REPO / "bin" / "anu-safe-mutation-v0.2.py"
RECOVERY = REPO / "bin" / "anu-recovery-v0.1.py"
SCHEMA = REPO / "migrations" / "0001_initial.sql"

APP = "def add(a, b):\n    return (a + b)\n"
UTIL = "def sub(a, b):\n    return (a - b)\n"
APP_CHANGE = {"file": "app.py", "old": "return (a + b)", "new": "return a + b"}
UTIL_CHANGE = {"file": "util.py", "old": "return (a - b)", "new": "return a - b"}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Env:
    def __init__(self, root):
        self.root = root
        self.sm = load(SAFE_MUTATION, f"sm_rec_{root.name}")
        self.rec = load(RECOVERY, f"rec_{root.name}")
        for module in (self.sm, self.rec):
            module.DB_FILE = root / "brain.db"
            module.LOG_FILE = root / "logs" / "log.jsonl"
            module.CHECKPOINT_ROOT = root / "checkpoints"
            module.WORKSPACE = root / "projects"
            module.EXECUTION_LOCK_FILE = root / "execution.lock"
        with sqlite3.connect(self.sm.DB_FILE) as db:
            db.executescript(SCHEMA.read_text(encoding="utf-8"))
        self.sm.ensure_state()
        self.project = self.sm.WORKSPACE / "demo"
        self.project.mkdir(parents=True)
        (self.project / "app.py").write_bytes(APP.encode())
        (self.project / "util.py").write_bytes(UTIL.encode())
        os.chmod(self.project / "app.py", 0o640)
        self.counter = 0

    def started(self, task_id=None):
        self.counter += 1
        tx_id = f"mut-test-{self.counter}"
        with sqlite3.connect(self.sm.DB_FILE) as db:
            db.execute(
                """INSERT INTO mutation_transactions
                   (project, file_path, operation, status, created_at,
                    transaction_id, task_id, details)
                   VALUES ('demo', 'app.py', 'python_test', 'STARTED', ?, ?, ?, '{}')""",
                (self.sm.now_iso(), tx_id, task_id),
            )
        return tx_id

    def checkpoint(self, tx_id, changes, record=True):
        self.sm.make_checkpoint("demo", self.project, changes, tx_id)
        if record:
            self.sm.checkpoint_event(tx_id, None, "PRE_MUTATION", "CHECKPOINT_CREATED", {})

    def apply(self, change):
        target = self.project / change["file"]
        text = target.read_text(encoding="utf-8")
        self.sm.atomic_write(target, text.replace(change["old"], change["new"], 1))

    def row(self, tx_id):
        with sqlite3.connect(self.sm.DB_FILE) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute(
                "SELECT * FROM mutation_transactions WHERE transaction_id = ?", (tx_id,)
            ).fetchone())

    def recover(self):
        return self.rec.recover()

    def read(self, name):
        return (self.project / name).read_bytes().decode()


def run(test):
    with tempfile.TemporaryDirectory() as tmp:
        test(Env(Path(tmp)))


def case_no_checkpoint_is_not_applied(env):
    tx = env.started()
    result = env.recover()
    assert result["status"] == "RECOVERED", result
    row = env.row(tx)
    assert row["status"] == "INTERRUPTED_NOT_APPLIED", row
    assert row["integrity_verified"] == 1
    assert row["rollback_attempted"] == 0
    assert row["completed_at"]
    assert env.read("app.py") == APP
    print("PASS no checkpoint: INTERRUPTED_NOT_APPLIED")


def case_recorded_checkpoint_without_manifest_is_conflict(env):
    tx = env.started()
    env.sm.checkpoint_event(tx, None, "PRE_MUTATION", "CHECKPOINT_CREATED", {})
    env.recover()
    row = env.row(tx)
    assert row["status"] == "INTERRUPTED_CONFLICT", row
    assert row["integrity_verified"] == 0
    assert "manifest is missing" in json.loads(row["details"])["recovery"]["reason"]
    print("PASS recorded checkpoint without manifest: INTERRUPTED_CONFLICT")


def case_baseline_file_is_not_applied(env):
    tx = env.started()
    env.checkpoint(tx, [APP_CHANGE])
    env.recover()
    row = env.row(tx)
    assert row["status"] == "INTERRUPTED_NOT_APPLIED", row
    assert row["checkpoint_path"].endswith(tx)
    assert env.read("app.py") == APP
    print("PASS checkpoint taken, file at baseline: INTERRUPTED_NOT_APPLIED")


def case_applied_file_is_rolled_back(env):
    tx = env.started()
    env.checkpoint(tx, [APP_CHANGE])
    env.apply(APP_CHANGE)
    assert env.read("app.py") != APP

    env.recover()
    row = env.row(tx)
    assert row["status"] == "INTERRUPTED_ROLLED_BACK", row
    assert row["integrity_verified"] == 1
    assert row["rollback_attempted"] == 1
    assert row["rollback_sha256"]
    assert env.read("app.py") == APP
    assert stat.S_IMODE((env.project / "app.py").stat().st_mode) == 0o640
    evidence = json.loads(row["details"])["recovery"]
    assert evidence["files"][0]["state"] == "applied", evidence
    print("PASS applied change: restored byte-identical with mode, INTERRUPTED_ROLLED_BACK")


def case_unknown_content_is_conflict_and_untouched(env):
    tx = env.started()
    env.checkpoint(tx, [APP_CHANGE])
    (env.project / "app.py").write_bytes(b"def add(a, b):\n    return 42\n")

    env.recover()
    row = env.row(tx)
    assert row["status"] == "INTERRUPTED_CONFLICT", row
    assert env.read("app.py") == "def add(a, b):\n    return 42\n"
    print("PASS unknown content: INTERRUPTED_CONFLICT, file untouched")


def case_corrupt_checkpoint_is_conflict_and_untouched(env):
    tx = env.started()
    env.checkpoint(tx, [APP_CHANGE])
    env.apply(APP_CHANGE)
    applied = env.read("app.py")
    (env.sm.CHECKPOINT_ROOT / "demo" / tx / "app.py").write_bytes(b"tampered\n")

    env.recover()
    row = env.row(tx)
    assert row["status"] == "INTERRUPTED_CONFLICT", row
    assert "integrity mismatch" in json.loads(row["details"])["recovery"]["reason"]
    assert env.read("app.py") == applied
    print("PASS corrupt checkpoint: INTERRUPTED_CONFLICT, file untouched")


def case_mixed_files_restore_only_applied(env):
    tx = env.started()
    env.checkpoint(tx, [APP_CHANGE, UTIL_CHANGE])
    env.apply(APP_CHANGE)

    env.recover()
    row = env.row(tx)
    assert row["status"] == "INTERRUPTED_ROLLED_BACK", row
    states = {f["path"]: f["state"] for f in json.loads(row["details"])["recovery"]["files"]}
    assert states == {"app.py": "applied", "util.py": "baseline"}, states
    assert env.read("app.py") == APP
    assert env.read("util.py") == UTIL
    print("PASS multi-file, one applied: only the applied file restored")


def case_any_unknown_file_means_touch_nothing(env):
    tx = env.started()
    env.checkpoint(tx, [APP_CHANGE, UTIL_CHANGE])
    env.apply(APP_CHANGE)
    applied = env.read("app.py")
    (env.project / "util.py").write_bytes(b"edited by someone else\n")

    env.recover()
    row = env.row(tx)
    assert row["status"] == "INTERRUPTED_CONFLICT", row
    assert env.read("app.py") == applied, "no file may be restored when any file is unknown"
    print("PASS multi-file with an unknown file: nothing restored, INTERRUPTED_CONFLICT")


def case_active_execution_defers(env):
    tx = env.started()
    env.checkpoint(tx, [APP_CHANGE])
    env.apply(APP_CHANGE)

    fd = os.open(env.rec.EXECUTION_LOCK_FILE, os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        result = env.recover()
    finally:
        os.close(fd)

    assert result == {"status": "DEFERRED_ACTIVE_EXECUTION"}, result
    assert env.row(tx)["status"] == "STARTED"
    assert env.read("app.py") != APP

    assert env.recover()["status"] == "RECOVERED"
    assert env.row(tx)["status"] == "INTERRUPTED_ROLLED_BACK"
    print("PASS active execution (shared lock held): recovery defers and touches nothing")


def case_finished_rows_untouched_and_rerun_clean(env):
    with sqlite3.connect(env.sm.DB_FILE) as db:
        db.execute(
            """INSERT INTO mutation_transactions
               (project, file_path, operation, status, created_at, transaction_id)
               VALUES ('demo', 'app.py', 'python_test', 'COMMITTED', 'x', 'mut-done')"""
        )
    tx = env.started()

    first = env.recover()
    assert [t["transaction_id"] for t in first["transactions"]] == [tx], first
    assert env.row("mut-done")["status"] == "COMMITTED"

    second = env.recover()
    assert second == {"status": "CLEAN", "transactions": [], "tasks": []}, second

    with sqlite3.connect(env.sm.DB_FILE) as db:
        events = db.execute(
            "SELECT count(*) FROM agent_events WHERE event_type = 'RECOVERY_TRANSACTION'"
        ).fetchone()[0]
    assert events == 1, events
    print("PASS finished transactions untouched; a second recovery is CLEAN")


if __name__ == "__main__":
    print("===== ANU V0.6.0 RECOVERY RECONCILER REGRESSION =====")

    for case in (
        case_no_checkpoint_is_not_applied,
        case_recorded_checkpoint_without_manifest_is_conflict,
        case_baseline_file_is_not_applied,
        case_applied_file_is_rolled_back,
        case_unknown_content_is_conflict_and_untouched,
        case_corrupt_checkpoint_is_conflict_and_untouched,
        case_mixed_files_restore_only_applied,
        case_any_unknown_file_means_touch_nothing,
        case_active_execution_defers,
        case_finished_rows_untouched_and_rerun_clean,
    ):
        run(case)

    print("RECOVERY_RECONCILER_TEST_COMPLETE")
