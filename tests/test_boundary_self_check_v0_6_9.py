#!/usr/bin/env python3
"""V0.6.9: boundary self-check logic (temporary paths)."""

import importlib.util
import os
import stat
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MOD = REPO / "bin" / "anu-boundary-check-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("boundary_t", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_tree():
    root = Path(tempfile.mkdtemp())
    for name in ("bin", "config", "src", "secrets", "tests"):
        p = root / name
        p.mkdir()
        # Make read-only (no write bit for owner).
        p.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
    for name in (
        "home", "brain", "workspace", "sandbox",
        "browser", "logs", "runtime", "checkpoints",
    ):
        (root / name).mkdir()
    return root


def test_boundary_ok_when_ro_and_rw_correct():
    m = load()
    root = make_tree()
    result = m.run_boundary_check(base=root)
    assert result["ok"] is True, result
    print("PASS boundary ok on correct tree")


def test_boundary_fails_when_secrets_writable():
    m = load()
    root = make_tree()
    secrets = root / "secrets"
    secrets.chmod(stat.S_IRWXU)
    result = m.run_boundary_check(base=root)
    assert result["ok"] is False, result
    secrets_row = [r for r in result["results"] if r["path"].endswith("secrets")][0]
    assert secrets_row["ok"] is False
    assert secrets_row["write_succeeded"] is True
    print("PASS boundary fails when secrets writable")


def test_boundary_fails_when_brain_readonly():
    m = load()
    root = make_tree()
    brain = root / "brain"
    brain.chmod(stat.S_IRUSR | stat.S_IXUSR)
    result = m.run_boundary_check(base=root)
    assert result["ok"] is False, result
    print("PASS boundary fails when brain not writable")


def test_policy_and_unit_list_secrets_tests_readonly():
    unit = (REPO / "deploy" / "systemd" / "anu-agent.service").read_text(
        encoding="utf-8"
    )
    policy = (REPO / "config" / "anu-policy.yaml").read_text(encoding="utf-8")
    assert "ReadOnlyPaths=/srv/anu/secrets" in unit
    assert "ReadOnlyPaths=/srv/anu/tests" in unit
    assert "ReadWritePaths=/srv/anu/secrets" not in unit
    assert "ReadWritePaths=/srv/anu/tests" not in unit
    assert "- /srv/anu/secrets" in policy
    assert "- /srv/anu/tests" in policy
    # secrets/tests must appear under read_only, not writable.
    writable_block = policy.split("writable:", 1)[1].split("read_only:", 1)[0]
    assert "/srv/anu/secrets" not in writable_block
    assert "/srv/anu/tests" not in writable_block
    print("PASS unit+policy G1/G2 path lists")


def main():
    print("===== ANU V0.6.9 BOUNDARY SELF CHECK =====")
    if os.name == "nt":
        # chmod RO directories behave differently on Windows; still run policy/unit.
        test_policy_and_unit_list_secrets_tests_readonly()
        print("SKIP path probes on Windows (chmod RO unreliable)")
    else:
        test_boundary_ok_when_ro_and_rw_correct()
        test_boundary_fails_when_secrets_writable()
        test_boundary_fails_when_brain_readonly()
        test_policy_and_unit_list_secrets_tests_readonly()
    print("BOUNDARY_SELF_CHECK_TEST_COMPLETE")


if __name__ == "__main__":
    main()
