#!/usr/bin/env python3
"""V0.8.11: host git safe.directory + skip status when project is not a git root."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
GATEWAY = REPO / "bin" / "anu-command-gateway.py"
MUTATION = REPO / "bin" / "anu-safe-mutation-v0.2.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gateway_safe_directory_star():
    gw = load(GATEWAY, "gw_0811")
    argv = gw.harden_host_argv("git_status_short", ["git", "status", "--short"])
    assert "safe.directory=*" in argv
    # Flags before subcommand
    assert argv.index("safe.directory=*") < argv.index("status")
    print("PASS gateway safe.directory=*")


def test_collect_skips_non_git_project():
    sm = load(MUTATION, "sm_0811")
    with TemporaryDirectory() as td:
        workspace = Path(td) / "workspace" / "projects"
        project = workspace / "sandbox-test-project"
        project.mkdir(parents=True)
        sm.WORKSPACE = workspace
        tx = {"details": {}}
        with mock.patch.object(sm, "run_gateway") as run_gw:
            out = sm._collect_actual_changed_files(
                "sandbox-test-project", task_id=1, tx=tx
            )
            run_gw.assert_not_called()
        assert out is None
        assert tx["details"]["git_status_short_skipped"] == "project_not_git_root"
    print("PASS collect skips non-git project")


def test_collect_calls_gateway_when_git_root():
    sm = load(MUTATION, "sm_0811b")
    with TemporaryDirectory() as td:
        workspace = Path(td) / "workspace" / "projects"
        project = workspace / "sandbox-test-project"
        project.mkdir(parents=True)
        (project / ".git").mkdir()
        sm.WORKSPACE = workspace
        tx = {"details": {}}

        def fake_gateway(project_name, command, task_id):
            return {"stdout": " M app.py\n"}

        with mock.patch.object(sm, "run_gateway", side_effect=fake_gateway):
            out = sm._collect_actual_changed_files(
                "sandbox-test-project", task_id=1, tx=tx
            )
        assert out == ["app.py"]
    print("PASS collect calls gateway when git root")


def test_collect_rejects_parent_escape_paths():
    sm = load(MUTATION, "sm_0811c")
    with TemporaryDirectory() as td:
        workspace = Path(td) / "workspace" / "projects"
        project = workspace / "sandbox-test-project"
        project.mkdir(parents=True)
        (project / ".git").mkdir()
        sm.WORKSPACE = workspace
        tx = {"details": {}}
        with mock.patch.object(
            sm,
            "run_gateway",
            return_value={"stdout": " M ../../../config/foo.yaml\n"},
        ):
            out = sm._collect_actual_changed_files(
                "sandbox-test-project", task_id=1, tx=tx
            )
        assert out is None
        assert tx["details"]["git_status_short_skipped"] == "paths_outside_project"
    print("PASS collect rejects parent-escape paths")


def main():
    test_gateway_safe_directory_star()
    test_collect_skips_non_git_project()
    test_collect_calls_gateway_when_git_root()
    test_collect_rejects_parent_escape_paths()
    print("ALL_PASS test_git_safe_directory_v0_8_11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
