#!/usr/bin/env python3

import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCHEDULER_RUNTIME = REPO / "bin" / "anu-scheduler-runtime-v0.1.py"

CHILD = textwrap.dedent(
    """
    import importlib.util, sys, time
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("sched_rt_child", sys.argv[1])
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    root = Path(sys.argv[2])

    def hold(db, max_tasks):
        (root / "holding").touch()
        time.sleep(120)

    rt.run_scheduler_cycle_once(str(root / "brain.db"), 1, hold, lock_path=str(root / "scheduler.lock"))
    """
)


def load_scheduler_runtime():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sched_rt_test", SCHEDULER_RUNTIME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wait_for(path, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def test_killed_lock_holder_releases_the_scheduler_lock():
    rt = load_scheduler_runtime()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "child.py").write_text(CHILD, encoding="utf-8")
        db_path = str(root / "brain.db")
        lock_path = str(root / "scheduler.lock")
        ran = []

        def cycle(db, max_tasks):
            ran.append(max_tasks)
            return {"status": "RAN"}

        process = subprocess.Popen(
            [sys.executable, str(root / "child.py"), str(SCHEDULER_RUNTIME), str(root)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            assert wait_for(root / "holding", 15), "child never took the lock"
            assert rt.run_scheduler_cycle_once(db_path, 1, cycle, lock_path=lock_path) == {
                "status": "SKIPPED_LOCKED"
            }
            assert ran == []

            process.send_signal(signal.SIGKILL)
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

        assert Path(lock_path).exists(), "the lock file stays on disk after the kill"
        assert rt.run_scheduler_cycle_once(db_path, 1, cycle, lock_path=lock_path) == {"status": "RAN"}
        assert ran == [1]

    print("PASS SIGKILLed lock holder: the leftover lock file does not block the next cycle")


if __name__ == "__main__":
    print("===== ANU V0.6.0 SCHEDULER LOCK RECOVERY REGRESSION =====")

    test_killed_lock_holder_releases_the_scheduler_lock()

    print("SCHEDULER_LOCK_RECOVERY_TEST_COMPLETE")
