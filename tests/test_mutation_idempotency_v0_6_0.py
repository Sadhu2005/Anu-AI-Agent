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
TASK_ENGINE = REPO / "bin" / "anu-task-engine-v0.5.3.py"
SCHEMA = REPO / "migrations" / "0001_initial.sql"

ORIGINAL = "def add(a, b):\n    return (a + b)\n"
CHANGED = "def add(a, b):\n    return a + b\n"
CHANGES = [{"file": "app.py", "old": "return (a + b)", "new": "return a + b"}]


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_safe_mutation(root):
    module = load_module(SAFE_MUTATION, f"anu_safe_mutation_idem_{root.name}")
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


class CountingGateway:
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return {"process_exit_code": 0, "result": {"ok": True}, "stdout": "", "stderr": ""}


def test_same_task_and_changes_commit_once():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module, project = load_safe_mutation(root)
        gateway = CountingGateway()
        module.run_gateway = gateway

        code, first = run_main(module, root, task_id=42)
        assert code == 0 and first["status"] == "COMMITTED", first
        calls_after_first = gateway.calls
        committed_bytes = (project / "app.py").read_bytes()
        assert (project / "app.py").read_text(encoding="utf-8") == CHANGED

        code, second = run_main(module, root, task_id=42)
        assert code == 0, (code, second)
        assert second["ok"] is True
        assert second["status"] == "ALREADY_COMMITTED", second
        assert second["details"]["committed_transaction_id"] == first["transaction_id"]
        assert second["idempotency_key"] == first["idempotency_key"]
        assert gateway.calls == calls_after_first, "no test may run for a committed key"
        assert (project / "app.py").read_bytes() == committed_bytes
        assert second["checkpoint_path"] is None

        statuses = [r["status"] for r in rows(module)]
        assert statuses == ["COMMITTED", "ALREADY_COMMITTED"], statuses

    print("PASS same task and changes: second run is ALREADY_COMMITTED, file untouched")


def test_without_task_id_there_is_no_deduplication():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module, project = load_safe_mutation(root)
        module.run_gateway = CountingGateway()

        for _ in range(2):
            (project / "app.py").write_text(ORIGINAL, encoding="utf-8")
            code, result = run_main(module, root)
            assert code == 0 and result["status"] == "COMMITTED", result
            assert result["idempotency_key"] is None

        statuses = [r["status"] for r in rows(module)]
        assert statuses == ["COMMITTED", "COMMITTED"], statuses

    print("PASS without a task id the pre-V0.6.0 behaviour is unchanged")


def test_key_is_deterministic_and_input_sensitive():
    with tempfile.TemporaryDirectory() as tmp:
        module, _ = load_safe_mutation(Path(tmp))
        key = module.idempotency_key

        base = key(1, "demo", "python_test", CHANGES)
        assert base == key(1, "demo", "python_test", [dict(CHANGES[0])])
        assert len(base) == 64

        variants = [
            key(2, "demo", "python_test", CHANGES),
            key(1, "other", "python_test", CHANGES),
            key(1, "demo", "python_compile", CHANGES),
            key(1, "demo", "python_test", [{**CHANGES[0], "file": "b.py"}]),
            key(1, "demo", "python_test", [{**CHANGES[0], "old": "x"}]),
            key(1, "demo", "python_test", [{**CHANGES[0], "new": "y"}]),
            key(1, "demo", "python_test", CHANGES + CHANGES),
        ]
        assert base not in variants
        assert len(set(variants)) == len(variants)
        assert key(None, "demo", "python_test", CHANGES) is None

    print("PASS idempotency key is deterministic and covers task, project, operation, changes")


def test_database_rejects_a_second_commit_for_a_key():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module, _ = load_safe_mutation(root)
        module.ensure_state()

        def insert(tx_id, status):
            with sqlite3.connect(module.DB_FILE) as db:
                db.execute(
                    """INSERT INTO mutation_transactions
                       (project, file_path, operation, status, created_at,
                        transaction_id, idempotency_key)
                       VALUES ('demo', 'app.py', 'python_test', ?, 'now', ?, 'k1')""",
                    (status, tx_id),
                )

        insert("mut-a", "COMMITTED")
        insert("mut-b", "ROLLED_BACK")
        insert("mut-c", "ALREADY_COMMITTED")
        try:
            insert("mut-d", "COMMITTED")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("a second COMMITTED row for the same key was accepted")

    print("PASS database allows at most one COMMITTED row per idempotency key")


def test_task_engine_treats_already_committed_as_completed():
    engine = load_module(TASK_ENGINE, "anu_task_engine_v0_5_3_idem_test")

    assert engine.classify_mutation({"status": "ALREADY_COMMITTED"}, 0) == "completed"
    assert engine.classify_mutation({"status": "COMMITTED"}, 0) == "completed"
    assert engine.classify_mutation({"status": "ALREADY_COMMITTED"}, 1) == "blocked"
    assert engine.classify_mutation({"status": "ROLLED_BACK"}, 1) == "failed"

    print("PASS Task Engine completes a task whose change is ALREADY_COMMITTED")


if __name__ == "__main__":
    print("===== ANU V0.6.0 MUTATION IDEMPOTENCY REGRESSION =====")

    test_same_task_and_changes_commit_once()
    test_without_task_id_there_is_no_deduplication()
    test_key_is_deterministic_and_input_sensitive()
    test_database_rejects_a_second_commit_for_a_key()
    test_task_engine_treats_already_committed_as_completed()

    print("MUTATION_IDEMPOTENCY_TEST_COMPLETE")
