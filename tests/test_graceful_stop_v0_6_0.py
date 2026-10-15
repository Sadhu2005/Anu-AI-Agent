#!/usr/bin/env python3

import importlib.util
import json
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "bin" / "anu-runtime.py"
UNIT = REPO / "deploy" / "systemd" / "anu-agent.service"

SAFE_MUTATION_TIMEOUT_SECONDS = 300


def load_runtime():
    spec = importlib.util.spec_from_file_location(
        "anu_runtime_v0_6_0_test",
        RUNTIME,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load runtime: {RUNTIME}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHILD = textwrap.dedent(
    """
    import importlib.util
    import sys
    import time
    from pathlib import Path

    tmp = Path(sys.argv[1])
    spec = importlib.util.spec_from_file_location(
        "anu_runtime_graceful_child", sys.argv[2]
    )
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)

    rt.LOG_FILE = tmp / "anu.log"
    rt.STATE_FILE = tmp / "anu-state.json"
    rt.INTERVAL_SECONDS = 1
    rt.run_resource_guardian = lambda: {
        "status": "NORMAL",
        "temperature_c": 40.0,
        "memory": {"available_mb": 1000},
    }

    def record(line):
        with (tmp / "cycles.log").open("a", encoding="utf-8") as f:
            f.write(line + "\\n")

    class SlowScheduler:
        @staticmethod
        def run_agent_scheduler_cycle_once(db_path, max_tasks, lock_path=None):
            record("start")
            (tmp / "in_cycle").touch()
            time.sleep(3)
            record("end")
            return []

    rt._load_scheduler = lambda: SlowScheduler()
    rt.main()
    """
)


def wait_for(path, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def test_sigterm_mid_cycle_finishes_cycle_and_stops():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        child = tmp / "child.py"
        child.write_text(CHILD, encoding="utf-8")

        process = subprocess.Popen(
            [sys.executable, str(child), str(tmp), str(RUNTIME)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            assert wait_for(tmp / "in_cycle", 15), "cycle never started"
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=20)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

        assert process.returncode == 0, (process.returncode, stderr)

        cycles = (tmp / "cycles.log").read_text(encoding="utf-8").split()
        assert cycles == ["start", "end"], cycles

        state = json.loads((tmp / "anu-state.json").read_text(encoding="utf-8"))
        assert state["status"] == "stopped", state

        log = (tmp / "anu.log").read_text(encoding="utf-8")
        assert "Runtime stopped." in log

    print("PASS SIGTERM mid-cycle: cycle completes, no new cycle, clean exit")


def test_stop_during_health_check_starts_no_cycle():
    runtime = load_runtime()

    messages = []
    runtime.write_log = messages.append
    runtime.save_state = lambda data: None

    def guardian_then_stop():
        runtime.running = False
        return {"status": "NORMAL", "temperature_c": 40.0, "memory": {}}

    def fail_loader():
        raise AssertionError("scheduler must not load after a stop request")

    runtime.run_resource_guardian = guardian_then_stop
    runtime._load_scheduler = fail_loader

    result = runtime.run_runtime_iteration()

    assert result == {"status": "SKIPPED_STOPPING"}, result
    assert "Scheduler skipped: stop requested" in messages

    print("PASS stop during health check starts no scheduler cycle")


def test_iteration_runs_scheduler_when_not_stopping():
    runtime = load_runtime()

    runtime.write_log = lambda message: None
    runtime.save_state = lambda data: None
    runtime.run_resource_guardian = lambda: {"status": "NORMAL", "memory": {}}

    class FakeScheduler:
        @staticmethod
        def run_agent_scheduler_cycle_once(db_path, max_tasks, lock_path=None):
            return []

    runtime._load_scheduler = lambda: FakeScheduler()

    assert runtime.running is True
    assert runtime.run_runtime_iteration() == []

    print("PASS iteration runs the scheduler when no stop is requested")


def test_unavailable_health_check_starts_no_cycle():
    runtime = load_runtime()

    messages = []
    runtime.write_log = messages.append
    runtime.run_resource_guardian = lambda: None

    def fail_loader():
        raise AssertionError("scheduler must not load without a health check")

    runtime._load_scheduler = fail_loader

    assert runtime.run_runtime_iteration() is None
    assert "Health check unavailable." in messages

    print("PASS unavailable health check starts no scheduler cycle")


def read_unit(path):
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key.strip(), []).append(value.strip())
    return values


def test_unit_stops_gracefully_before_sandbox():
    unit = read_unit(UNIT)

    assert unit.get("KillMode") == ["mixed"], unit.get("KillMode")

    timeout = unit.get("TimeoutStopSec")
    assert timeout and len(timeout) == 1, timeout
    assert int(timeout[0]) > SAFE_MUTATION_TIMEOUT_SECONDS, timeout

    after = " ".join(unit.get("After", [])).split()
    wants = " ".join(unit.get("Wants", [])).split()
    assert "anu-sandbox.service" in after, after
    assert "anu-sandbox.service" in wants, wants

    print("PASS unit: KillMode=mixed, stop timeout above 300 s, ordered after sandbox")


if __name__ == "__main__":
    print("===== ANU V0.6.0 GRACEFUL STOP REGRESSION =====")

    test_sigterm_mid_cycle_finishes_cycle_and_stops()
    test_stop_during_health_check_starts_no_cycle()
    test_iteration_runs_scheduler_when_not_stopping()
    test_unavailable_health_check_starts_no_cycle()
    test_unit_stops_gracefully_before_sandbox()

    print("GRACEFUL_STOP_TEST_COMPLETE")
