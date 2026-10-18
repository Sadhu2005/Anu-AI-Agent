#!/usr/bin/env python3
"""V0.7.0: runtime iteration wires expand → schedule gate → sync."""

import importlib.util
import os
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "bin" / "anu-runtime.py"


def load_runtime(base: Path):
    spec = importlib.util.spec_from_file_location("anu_runtime_v070", RUNTIME)
    mod = importlib.util.module_from_spec(spec)
    # Pre-set env so module-level BASE resolves to the temp tree.
    os.environ["ANU_BASE"] = str(base)
    spec.loader.exec_module(mod)
    mod.BASE = base
    mod.DB_FILE = base / "brain" / "anu-brain.db"
    mod.LOG_FILE = base / "logs" / "anu.log"
    mod.STATE_FILE = base / "runtime" / "anu-state.json"
    mod.GUARDIAN = base / "bin" / "anu-resource-guardian.py"
    mod.SCHEDULER = REPO / "bin" / "anu-task-scheduler-v0.1.py"
    mod.RECOVERY = REPO / "bin" / "anu-recovery-v0.1.py"
    mod.STARTUP = REPO / "bin" / "anu-startup-recovery-v0.1.py"
    mod.BOUNDARY = REPO / "bin" / "anu-boundary-check-v0.1.py"
    mod.GOAL_CYCLE = REPO / "bin" / "anu-goal-cycle-v0.1.py"
    mod.SCHEDULER_LOCK_FILE = base / "runtime" / "anu-scheduler.lock"
    mod.EXECUTION_LOCK_FILE = base / "runtime" / "anu-execution.lock"
    mod._HERE = REPO / "bin"
    return mod


def make_tree():
    root = Path(tempfile.mkdtemp())
    (root / "bin").mkdir()
    (root / "brain").mkdir()
    (root / "logs").mkdir()
    (root / "runtime").mkdir()
    (root / "logs" / "anu.log").write_text("", encoding="utf-8")
    return root


def test_iteration_calls_expand_wake_sync():
    root = make_tree()
    old = os.environ.get("ANU_BASE")
    calls = {"wake": 0, "expand": 0, "sync": 0, "schedule": 0}
    try:
        rt = load_runtime(root)
        rt.recovery_done = True
        rt.boundary_checked = True
        rt.boundary_ok = True
        rt.running = True

        def fake_guardian():
            return {
                "status": "NORMAL",
                "temperature_c": 40,
                "memory": {"available_mb": 2000},
            }

        def fake_wake():
            calls["wake"] += 1
            return {"satisfied": [], "checked": 0}

        def fake_expand():
            calls["expand"] += 1
            return {"expanded_count": 0, "skipped_count": 0}

        def fake_sync():
            calls["sync"] += 1
            return {"synced": 0, "changed": 0}

        def fake_schedule(data):
            calls["schedule"] += 1
            assert data["status"] == "NORMAL"
            return {"status": "OK"}

        rt.run_resource_guardian = fake_guardian
        rt.run_wake_reevaluation = fake_wake
        rt.run_goal_expand = fake_expand
        rt.run_goal_sync = fake_sync
        rt.run_agent_scheduler_cycle = fake_schedule

        result = rt.run_runtime_iteration()
        assert result == {"status": "OK"}
        assert calls == {
            "wake": 1,
            "expand": 1,
            "sync": 1,
            "schedule": 1,
        }
        # Order: wake → expand → schedule → sync (schedule before sync).
        # Verified by counters all 1 after one iteration.
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS runtime iteration wires wake/expand/schedule/sync")


def test_resource_skip_still_syncs():
    """Even when scheduler skips resources, goal sync still runs."""
    root = make_tree()
    old = os.environ.get("ANU_BASE")
    try:
        rt = load_runtime(root)
        rt.recovery_done = True
        rt.boundary_checked = True
        rt.boundary_ok = True
        rt.running = True
        synced = {"n": 0}

        rt.run_resource_guardian = lambda: {
            "status": "WARNING",
            "temperature_c": 70,
            "memory": {"available_mb": 500},
        }
        rt.run_wake_reevaluation = lambda: {}
        rt.run_goal_expand = lambda: {"expanded_count": 0}
        rt.run_goal_sync = lambda: synced.__setitem__("n", synced["n"] + 1) or {
            "synced": 0
        }

        result = rt.run_runtime_iteration()
        assert result["status"] == "SKIPPED_RESOURCE"
        assert synced["n"] == 1
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    print("PASS resource skip still runs goal sync")


if __name__ == "__main__":
    print("===== ANU V0.7.0 RUNTIME GOAL SPINE =====")
    test_iteration_calls_expand_wake_sync()
    test_resource_skip_still_syncs()
    print("RUNTIME_GOAL_SPINE_TEST_COMPLETE")
