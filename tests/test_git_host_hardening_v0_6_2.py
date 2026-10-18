#!/usr/bin/env python3
"""F4: host git invocations use hardened argv and minimal env."""

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-command-gateway.py"


def load():
    spec = importlib.util.spec_from_file_location("gateway_f4_v062", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_harden_host_argv_git_flags():
    mod = load()
    argv = mod.harden_host_argv("git_status", ["git", "status"])
    assert argv[0] == "git"
    assert argv[1:5] == [
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
    ]
    assert argv[5:] == ["status"]

    diff_argv = mod.harden_host_argv("git_diff", ["git", "diff", "HEAD"])
    assert "core.fsmonitor=false" in diff_argv
    assert "core.hooksPath=/dev/null" in diff_argv
    assert "--no-ext-diff" in diff_argv
    assert "--no-textconv" in diff_argv

    non_git = mod.harden_host_argv("python_test", ["python3", "-c", "print(1)"])
    assert non_git == ["python3", "-c", "print(1)"]
    print("PASS harden_host_argv injects git -c flags")


def test_host_command_env_minimal():
    mod = load()
    env = mod.host_command_env()
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["PATH"] == "/usr/bin:/bin"
    assert "HOME" in env
    print("PASS host_command_env is minimal and disables system git config")


def test_fsmonitor_flags_precede_subcommand():
    mod = load()
    argv = mod.harden_host_argv("custom", ["git", "rev-parse", "HEAD"])
    fs_idx = argv.index("core.fsmonitor=false")
    rev_idx = argv.index("rev-parse")
    assert fs_idx < rev_idx
    print("PASS fsmonitor/hooksPath -c flags precede git subcommand")


if __name__ == "__main__":
    print("===== ANU F4 GIT HOST HARDENING REGRESSION =====")
    test_harden_host_argv_git_flags()
    test_host_command_env_minimal()
    test_fsmonitor_flags_precede_subcommand()
    print("GIT_HOST_HARDENING_TEST_COMPLETE")
