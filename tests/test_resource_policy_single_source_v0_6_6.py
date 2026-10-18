#!/usr/bin/env python3
"""V0.6.6: authoritative resource policy + consistency gate."""

import importlib.util
import os
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-resource-policy-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("rp_ss", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_load_authoritative_policy():
    m = load()
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(REPO)
        policy = m.load_resource_policy()
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    assert policy["warning_celsius"] == 60
    assert policy["pause_celsius"] == 65
    assert policy["resume_celsius"] == 55
    assert policy["max_attempts_per_task"] == 3
    assert policy["heavy_workload_ram_threshold_mb"] == 1024
    assert policy["minimum_free_gb"] == 50
    print("PASS load authoritative anu-resources.yaml")


def test_consistency_gate_against_repo_configs():
    m = load()
    old = os.environ.get("ANU_BASE")
    try:
        os.environ["ANU_BASE"] = str(REPO)
        result = m.assert_policy_consistency()
    finally:
        if old is None:
            os.environ.pop("ANU_BASE", None)
        else:
            os.environ["ANU_BASE"] = old
    assert result["ok"], result["errors"]
    print("PASS policy consistency gate")


def test_consistency_detects_drift():
    m = load()
    root = Path(tempfile.mkdtemp())
    cfg = root / "config"
    cfg.mkdir()
    (cfg / "anu-resources.yaml").write_text(
        """
resource_policy:
  temperature:
    warning_celsius: 60
    pause_celsius: 65
    resume_celsius: 55
  memory:
    max_mb: 3072
    emergency_free_mb: 1024
    minimum_free_ram_mb: 2048
  storage:
    minimum_free_gb: 50
  cpu:
    max_percent: 50
  execution:
    max_attempts_per_task: 3
    heavy_workload_ram_threshold_mb: 1024
    heavy_workload_cpu_threshold_percent: 60
  agent:
    max_parallel_tasks: 1
""",
        encoding="utf-8",
    )
    (cfg / "anu-admission-policy.yaml").write_text(
        """
host_safety:
  minimum_free_ram_mb: 9999
  minimum_free_disk_gb: 50
  warning_temperature_c: 60
  pause_temperature_c: 65
anu_budget:
  maximum_ram_mb: 3072
  maximum_cpu_percent: 50
execution:
  heavy_workload_ram_threshold_mb: 1024
  heavy_workload_cpu_threshold_percent: 60
""",
        encoding="utf-8",
    )
    (cfg / "anu-policy.yaml").write_text(
        """
resources:
  max_cpu_percent: 50
  max_memory_mb: 3072
  max_parallel_tasks: 1
  temperature_limit_celsius: 65
execution:
  max_attempts_per_task: 3
""",
        encoding="utf-8",
    )
    result = m.assert_policy_consistency(
        resources_path=cfg / "anu-resources.yaml",
        admission_path=cfg / "anu-admission-policy.yaml",
        policy_path=cfg / "anu-policy.yaml",
    )
    assert not result["ok"]
    assert any("minimum_free_ram_mb" in e for e in result["errors"])
    print("PASS consistency detects drift")


def main():
    print("===== ANU V0.6.6 RESOURCE POLICY SINGLE SOURCE =====")
    test_load_authoritative_policy()
    test_consistency_gate_against_repo_configs()
    test_consistency_detects_drift()
    print("RESOURCE_POLICY_SINGLE_SOURCE_TEST_COMPLETE")


if __name__ == "__main__":
    main()
