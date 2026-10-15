#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import json
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SAFE_MUTATION = REPO / "bin" / "anu-safe-mutation-v0.2.py"
RECOVERY = REPO / "bin" / "anu-recovery-v0.1.py"
SCHEMA = REPO / "migrations" / "0001_initial.sql"

ORIGINAL = "def add(a, b):\n    return (a + b)\n"
CHANGED = "def add(a, b):\n    return a + b\n"
CHANGES = [{"file": "app.py", "old": "return (a + b)", "new": "return a + b"}]
TASK_ID = 77

# A real Safe Mutation process. Only the sandbox gateway is replaced: the
# gateway call numbered HANG_ON_CALL marks the moment and then hangs.
CHILD = textwrap.dedent(
    """
    import importlib.util, sys, time
    from pathlib import Path

    root, safe_mutation, hang_on_call = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    spec = importlib.util.spec_from_file_location("sm_crash_child", safe_mutation)
    sm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sm)
    sm.DB_FILE = root / "brain.db"
    sm.LOG_FILE = root / "logs" / "mutations.jsonl"
    sm.CHECKPOINT_ROOT = root / "checkpoints"
    sm.WORKSPACE = root / "projects"
    sm.EXECUTION_LOCK_FILE = root / "execution.lock"

    calls = []

    def gateway(*args, **kwargs):
        calls.append(1)
        if len(calls) == hang_on_call:
            (root / "hanging").touch()
            time.sleep(120)
        return {"process_exit_code": 0, "result": {"ok": True}, "stdout": "", "stderr": ""}

    sm.run_gateway = gateway
    sys.argv = ["anu-safe-mutation-v0.2.py", "--project", "demo",
                "--changes-file", str(root / "changes.json"),
                "--task-id", sys.argv[4], "--operation", "python_test"]
    sm.main()
    """
)


def load(path, name, root):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.DB_FILE = root / "brain.db"
    module.LOG_FILE = root / "logs" / "log.jsonl"
    module.CHECKPOINT_ROOT = root / "checkpoints"
    module.WORKSPACE = root / "projects"
    module.EXECUTION_LOCK_FILE = root / "execution.lock"
    return module


def setup(root):
    with sqlite3.connect(root / "brain.db") as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
    project = root / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "app.py").write_bytes(ORIGINAL.encode())
    (root / "changes.json").write_text(json.dumps(CHANGES), encoding="utf-8")
    (root / "child.py").write_text(CHILD, encoding="utf-8")
    return project


def wait_for(path, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def statuses(root):
    with sqlite3.connect(root / "brain.db") as db:
        return [r[0] for r in db.execute("SELECT status FROM mutation_transactions ORDER BY id")]


def kill_mid_transaction(root, hang_on_call, sig):
    process = subprocess.Popen(
        [sys.executable, str(root / "child.py"), str(root), str(SAFE_MUTATION),
         str(hang_on_call), str(TASK_ID)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert wait_for(root / "hanging", 30), process.stderr.read() if process.poll() is not None else "child never reached the hang point"
        rec = load(RECOVERY, f"rec_live_{root.name}_{hang_on_call}", root)
        assert rec.recover() == {"status": "DEFERRED_ACTIVE_EXECUTION"}, \
            "recovery must not touch a live transaction"
        process.send_signal(sig)
        process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert process.returncode == -sig, process.returncode


def run_again(root):
    sm = load(SAFE_MUTATION, f"sm_again_{root.name}_{time.monotonic_ns()}", root)
    sm.run_gateway = lambda *a, **k: {"process_exit_code": 0, "result": {"ok": True},
                                      "stdout": "", "stderr": ""}
    saved = sys.argv
    sys.argv = ["anu-safe-mutation-v0.2.py", "--project", "demo",
                "--changes-file", str(root / "changes.json"),
                "--task-id", str(TASK_ID), "--operation", "python_test"]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            sm.main()
    except SystemExit as exc:
        code = exc.code
    finally:
        sys.argv = saved
    return code, json.loads(out.getvalue())


def test_sigkill_after_apply_rolls_back_then_commits_once():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = setup(root)

        # Call 1 is the baseline test, call 2 the post-mutation test.
        kill_mid_transaction(root, hang_on_call=2, sig=signal.SIGKILL)
        assert (project / "app.py").read_text(encoding="utf-8") == CHANGED, \
            "precondition: the change was applied when the process died"
        assert statuses(root) == ["STARTED"], statuses(root)

        rec = load(RECOVERY, f"rec_after_{root.name}", root)
        result = rec.recover()
        assert [t["status"] for t in result["transactions"]] == ["INTERRUPTED_ROLLED_BACK"], result
        assert (project / "app.py").read_bytes() == ORIGINAL.encode()

        code, retry = run_again(root)
        assert code == 0 and retry["status"] == "COMMITTED", retry
        code, again = run_again(root)
        assert code == 0 and again["status"] == "ALREADY_COMMITTED", again
        assert (project / "app.py").read_text(encoding="utf-8") == CHANGED

        assert statuses(root) == ["INTERRUPTED_ROLLED_BACK", "COMMITTED", "ALREADY_COMMITTED"], statuses(root)

    print("PASS SIGKILL after apply: recovery restores baseline, retry commits exactly once")


def test_sigterm_during_baseline_is_not_applied():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = setup(root)

        kill_mid_transaction(root, hang_on_call=1, sig=signal.SIGTERM)
        assert statuses(root) == ["STARTED"], statuses(root)
        assert not (root / "checkpoints").exists() or not any((root / "checkpoints").rglob("manifest.json"))

        rec = load(RECOVERY, f"rec_baseline_{root.name}", root)
        result = rec.recover()
        assert [t["status"] for t in result["transactions"]] == ["INTERRUPTED_NOT_APPLIED"], result
        assert (project / "app.py").read_bytes() == ORIGINAL.encode()

    print("PASS SIGTERM during baseline: INTERRUPTED_NOT_APPLIED, file never changed")


if __name__ == "__main__":
    print("===== ANU V0.6.0 CRASH MID-MUTATION RECOVERY REGRESSION =====")

    test_sigkill_after_apply_rolls_back_then_commits_once()
    test_sigterm_during_baseline_is_not_applied()

    print("CRASH_MID_MUTATION_RECOVERY_TEST_COMPLETE")
