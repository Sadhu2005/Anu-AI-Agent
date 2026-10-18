#!/usr/bin/env python3
"""V0.6.8: graceful shutdown parks state; log rotation bounds growth."""

import importlib.util
import json
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-startup-recovery-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("shutdown_t", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_park_writes_durable_state():
    m = load()
    root = Path(tempfile.mkdtemp())
    state = root / "anu-state.json"
    m.write_heartbeat(state, extra={"status": "running"})
    parked = m.park_for_shutdown(state, reason="SIGTERM")
    assert parked["status"] == "parked"
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["park_reason"] == "SIGTERM"
    assert "parked_at" in data
    print("PASS park writes durable state")


def test_log_rotation():
    m = load()
    root = Path(tempfile.mkdtemp())
    big = root / "anu-mutations.jsonl"
    big.write_bytes(b"x" * 1000)
    small = root / "anu-tiny.jsonl"
    small.write_text("ok\n", encoding="utf-8")
    result = m.rotate_logs(log_dir=root, max_bytes=500)
    assert any(r["path"].endswith("anu-mutations.jsonl") for r in result["rotated"])
    assert (root / "anu-mutations.jsonl.1").is_file()
    assert big.stat().st_size == 0
    assert small.read_text(encoding="utf-8") == "ok\n"
    print("PASS log rotation bounds growth")


def main():
    print("===== ANU V0.6.8 GRACEFUL SHUTDOWN =====")
    test_park_writes_durable_state()
    test_log_rotation()
    print("GRACEFUL_SHUTDOWN_TEST_COMPLETE")


if __name__ == "__main__":
    main()
