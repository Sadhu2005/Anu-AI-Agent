#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SAFE_MUTATION = REPO / "bin" / "anu-safe-mutation-v0.2.py"
SCHEMA = REPO / "migrations" / "0001_initial.sql"

ORIGINAL = "def add(a, b):\n    return (a + b)\n"
CHANGED = "def add(a, b):\n    return a + b\n"
CHANGES = [{"file": "app.py", "old": "return (a + b)", "new": "return a + b"}]


def load_safe_mutation(root):
    spec = importlib.util.spec_from_file_location(
        f"anu_safe_mutation_v0_6_0_{root.name}",
        SAFE_MUTATION,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.DB_FILE = root / "brain.db"
    module.LOG_FILE = root / "logs" / "anu-mutations.jsonl"
    module.CHECKPOINT_ROOT = root / "checkpoints"
    module.WORKSPACE = root / "projects"

    with sqlite3.connect(module.DB_FILE) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))

    project = module.WORKSPACE / "demo"
    project.mkdir(parents=True)
    (project / "app.py").write_text(ORIGINAL, encoding="utf-8")
    (root / "changes.json").write_text(json.dumps(CHANGES), encoding="utf-8")
    return module, project


def ok_gateway(*args, **kwargs):
    return {"process_exit_code": 0, "result": {"ok": True}, "stdout": "", "stderr": ""}


def run_main(module, root, task_id=None):
    argv = [
        "anu-safe-mutation-v0.2.py",
        "--project", "demo",
        "--changes-file", str(root / "changes.json"),
        "--operation", "python_test",
    ]
    if task_id is not None:
        argv += ["--task-id", str(task_id)]

    saved = sys.argv
    sys.argv = argv
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            module.main()
    except SystemExit as exc:
        code = exc.code
    else:
        code = None
    finally:
        sys.argv = saved
    return code, json.loads(out.getvalue())


def rows(module):
    with sqlite3.connect(module.DB_FILE) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(
            "SELECT * FROM mutation_transactions ORDER BY id"
        )]


def test_started_row_exists_before_any_file_change():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module, project = load_safe_mutation(root)
        seen = []

        def gateway(*args, **kwargs):
            if not seen:
                current = rows(module)
                assert len(current) == 1, current
                assert current[0]["status"] == "STARTED", current[0]
                assert current[0]["completed_at"] is None
                assert current[0]["idempotency_key"], current[0]
                assert (project / "app.py").read_text(encoding="utf-8") == ORIGINAL
                seen.append(current[0])
            return ok_gateway()

        module.run_gateway = gateway
        code, result = run_main(module, root, task_id=7)

        assert code == 0, (code, result)
        assert result["status"] == "COMMITTED", result
        assert seen, "baseline gateway was never called"

        final = rows(module)
        assert len(final) == 1, final
        assert final[0]["id"] == seen[0]["id"]
        assert final[0]["transaction_id"] == result["transaction_id"]
        assert final[0]["status"] == "COMMITTED"
        assert final[0]["completed_at"]
        assert final[0]["integrity_verified"] == 1
        assert (project / "app.py").read_text(encoding="utf-8") == CHANGED

    print("PASS STARTED row is written before any file change, then finalized in place")


def test_rollback_finalizes_the_same_row():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module, project = load_safe_mutation(root)
        calls = []

        def gateway(*args, **kwargs):
            calls.append(1)
            ok = len(calls) != 2
            return {"process_exit_code": 0 if ok else 1, "result": {"ok": ok},
                    "stdout": "", "stderr": ""}

        module.run_gateway = gateway
        code, result = run_main(module, root, task_id=8)

        assert code == 1, (code, result)
        assert result["status"] == "ROLLED_BACK", result
        final = rows(module)
        assert len(final) == 1, final
        assert final[0]["status"] == "ROLLED_BACK"
        assert final[0]["rollback_verified"] == 1
        assert (project / "app.py").read_text(encoding="utf-8") == ORIGINAL

    print("PASS rolled-back transaction finalizes its STARTED row")


def test_baseline_abort_finalizes_the_same_row():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module, project = load_safe_mutation(root)
        module.run_gateway = lambda *a, **k: {
            "process_exit_code": 1, "result": {"ok": False}, "stdout": "", "stderr": ""
        }

        code, result = run_main(module, root, task_id=9)

        assert code == 2, (code, result)
        final = rows(module)
        assert len(final) == 1, final
        assert final[0]["status"] == "ABORTED_BASELINE_FAILURE"
        assert (project / "app.py").read_text(encoding="utf-8") == ORIGINAL

    print("PASS baseline abort finalizes its STARTED row")


def test_no_write_ahead_record_means_no_change():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module, project = load_safe_mutation(root)
        called = []

        def broken_record(tx):
            raise sqlite3.OperationalError("database is locked")

        def gateway(*args, **kwargs):
            called.append(1)
            return ok_gateway()

        module.record_started = broken_record
        module.run_gateway = gateway

        saved = sys.argv
        sys.argv = [
            "anu-safe-mutation-v0.2.py", "--project", "demo",
            "--changes-file", str(root / "changes.json"), "--task-id", "10",
        ]
        try:
            module.main()
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("main() must not continue without a write-ahead record")
        finally:
            sys.argv = saved

        assert not called, "gateway ran without a write-ahead record"
        assert rows(module) == []
        assert (project / "app.py").read_text(encoding="utf-8") == ORIGINAL

    print("PASS a failed write-ahead record stops the transaction before any change")


if __name__ == "__main__":
    print("===== ANU V0.6.0 MUTATION WRITE-AHEAD REGRESSION =====")

    test_started_row_exists_before_any_file_change()
    test_rollback_finalizes_the_same_row()
    test_baseline_abort_finalizes_the_same_row()
    test_no_write_ahead_record_means_no_change()

    print("MUTATION_WRITE_AHEAD_TEST_COMPLETE")
