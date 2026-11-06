#!/usr/bin/env python3
"""V1.5 handoff: a finished Hermes result becomes file/old/new, or it aborts.

Nothing in this file applies a change, commits, or pushes.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
TIP = "b75cfa8"


def _load(filename: str, name: str):
    path = BIN / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(**overrides):
    fields = {
        "job_id": "anu-goal-001-task-003",
        "status": "completed",
        "changed_files": ["src/labapp/clamp.py"],
        "files": [{
            "file": "src/labapp/clamp.py",
            "old": "old\n",
            "new": "def clamp(value, low, high):\n    return max(low, min(value, high))\n",
        }],
        "base_commit": TIP,
        "logs": "fixture",
        "commit": True,
        "push": True,
    }
    fields.update(overrides)
    return fields


class TestHermesHandoffUnitV15(unittest.TestCase):
    def setUp(self):
        self.result = _load("anu-hermes-result-v0.1.py", "hermes_result_v15_unit")

    def _run(self, payload, workspace, tip=TIP):
        return self.result.prepare_mutation(payload, workspace, tip)

    def test_tip_mismatch_aborts_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(_payload(), Path(tmp), "other-sha")
        self.assertFalse(out["ok"])
        self.assertFalse(out["applied"])
        self.assertFalse(out["commit"])
        self.assertFalse(out["push"])
        self.assertEqual(out["changes"], [])
        self.assertIn("project tip", out["reason"])
        print("PASS unit tip mismatch aborts")

    def test_secret_path_rejected(self):
        payload = _payload(
            changed_files=["secrets/github-token"],
            files=[{"file": "secrets/github-token", "old": "", "new": "token\n"}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(payload, Path(tmp))
        self.assertFalse(out["ok"])
        self.assertEqual(out["changes"], [])
        self.assertIn("secret path", out["reason"])
        print("PASS unit secret path rejected")

    def test_protected_path_rejected(self):
        payload = _payload(
            changed_files=["deploy/anu-hermes.service"],
            files=[{"file": "deploy/anu-hermes.service", "old": "a\n", "new": "b\n"}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(payload, Path(tmp))
        self.assertFalse(out["ok"])
        self.assertIn("protected path", out["reason"])
        print("PASS unit protected path rejected")

    def test_delete_rejected(self):
        payload = _payload(
            files=[{"file": "src/labapp/clamp.py", "old": "old\n", "new": ""}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(payload, Path(tmp))
        self.assertFalse(out["ok"])
        self.assertIn("delete rejected", out["reason"])
        self.assertFalse(out["commit"])
        self.assertFalse(out["push"])
        print("PASS unit delete rejected")

    def test_secret_content_rejected(self):
        leaked = "api_key=" + ("a" * 16)
        payload = _payload(
            files=[{"file": "src/labapp/clamp.py", "old": "old\n", "new": leaked + "\n"}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run(payload, Path(tmp))
        self.assertFalse(out["ok"])
        self.assertIn("secret content", out["reason"])
        print("PASS unit secret content rejected")

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "job"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()
            (outside / "secret").write_text("nope\n", encoding="utf-8")
            link = workspace / "leak"
            link.write_text("local\n", encoding="utf-8")
            created = False
            try:
                link.unlink()
                link.symlink_to(outside / "secret", target_is_directory=False)
                created = True
            except OSError:
                created = False
            payload = _payload(
                changed_files=["leak"],
                files=[{"file": "leak", "old": "", "new": "x\n"}],
            )
            if not created:
                real_is_symlink = Path.is_symlink
                real_resolve = Path.resolve

                def is_symlink(path):
                    if Path(path).name == "leak":
                        return True
                    return real_is_symlink(path)

                def resolve(path, *args, **kwargs):
                    if Path(path).name == "leak":
                        return (outside / "secret").resolve()
                    return real_resolve(path, *args, **kwargs)

                Path.is_symlink = is_symlink
                Path.resolve = resolve
                try:
                    out = self._run(payload, workspace)
                finally:
                    Path.resolve = real_resolve
                    Path.is_symlink = real_is_symlink
            else:
                out = self._run(payload, workspace)
        self.assertFalse(out["ok"])
        self.assertEqual(out["changes"], [])
        self.assertIn("symlink", out["reason"])
        print("PASS unit symlink escape rejected")

    def test_ready_mutation_is_unapplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / "src" / "labapp" / "clamp.py"
            target.parent.mkdir(parents=True)
            target.write_text("old\n", encoding="utf-8")
            out = self._run(_payload(), workspace)
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
        self.assertTrue(out["ok"])
        self.assertFalse(out["applied"])
        self.assertFalse(out["commit"])
        self.assertFalse(out["push"])
        self.assertEqual(out["base_commit"], TIP)
        self.assertEqual(out["changes"], [{
            "file": "src/labapp/clamp.py",
            "old": "old\n",
            "new": "def clamp(value, low, high):\n    return max(low, min(value, high))\n",
        }])
        print("PASS unit ready mutation stays unapplied")


class TestHermesHandoffIntegrationV15(unittest.TestCase):
    def test_handoff_leaves_brain_sentinel_and_goal_cycle_unwired(self):
        result = _load("anu-hermes-result-v0.1.py", "hermes_result_v15_int")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain.txt"
            brain.write_text("sentinel\n", encoding="utf-8")
            workspace = root / "job"
            workspace.mkdir()
            out = result.prepare_mutation(_payload(), workspace, TIP)
            self.assertEqual(brain.read_text(encoding="utf-8"), "sentinel\n")
        self.assertTrue(out["ok"])
        cycle = (BIN / "anu-goal-cycle-v0.1.py").read_text(encoding="utf-8")
        self.assertNotIn("prepare_mutation", cycle)
        self.assertNotIn("anu-hermes-result", cycle)
        print("PASS integration handoff leaves brain unwired")


class TestHermesHandoffFunctionalV15(unittest.TestCase):
    def test_completed_fixture_does_not_touch_disk(self):
        result = _load("anu-hermes-result-v0.1.py", "hermes_result_v15_fn")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / "src" / "labapp" / "clamp.py"
            target.parent.mkdir(parents=True)
            target.write_text("old\n", encoding="utf-8")
            before = target.read_bytes()
            out = result.prepare_mutation(_payload(), workspace, TIP)
            self.assertEqual(target.read_bytes(), before)
        self.assertEqual(out["reason"], "ready")
        self.assertFalse(out["applied"])
        self.assertFalse(out["commit"])
        self.assertFalse(out["push"])
        print("PASS functional completed handoff stays on disk")


def _ok_verify(*_args, **_kwargs):
    return {"process_exit_code": 0, "result": {"ok": True}, "stdout": "", "stderr": ""}


def _project(root: Path) -> tuple[Path, Path]:
    project = root / "projects" / "sandbox-test-project"
    target = project / "src" / "labapp" / "clamp.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8", newline="\n")
    return project, target


class TestHermesApplyV15(unittest.TestCase):
    def setUp(self):
        self.result = _load("anu-hermes-result-v0.1.py", "hermes_result_v15_apply")

    def test_aborted_handoff_does_not_call_engine(self):
        calls = []
        handoff = self.result.prepare_mutation(_payload(), Path("."), "other-sha")
        with tempfile.TemporaryDirectory() as tmp:
            project, target = _project(Path(tmp))
            before = target.read_bytes()
            out = self.result.apply_ready_handoff(
                handoff,
                project,
                verify=_ok_verify,
                engine=lambda changes: calls.append(changes) or {},
            )
            self.assertEqual(target.read_bytes(), before)
        self.assertEqual(calls, [])
        self.assertEqual(out["status"], "NOT_APPLIED")
        self.assertFalse(out["eligible_commit"])
        self.assertFalse(out["git_push_ok"])
        self.assertFalse(out["commit"])
        self.assertFalse(out["push"])
        print("PASS apply aborted handoff never reaches the engine")

    def test_production_path_does_not_call_engine(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            project, _target = _project(Path(tmp))
            handoff = self.result.prepare_mutation(_payload(), project, TIP)
        out = self.result.apply_ready_handoff(
            handoff,
            Path("/srv/anu/workspace/projects/sandbox-test-project"),
            verify=_ok_verify,
            engine=lambda changes: calls.append(changes) or {},
        )
        self.assertEqual(calls, [])
        self.assertEqual(out["reason"], "production path refused")
        self.assertFalse(out["git_push_ok"])
        print("PASS apply refuses /srv/anu")

    def test_ready_handoff_commits_without_git_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, target = _project(Path(tmp))
            handoff = self.result.prepare_mutation(_payload(), project, TIP)
            out = self.result.apply_ready_handoff(handoff, project, verify=_ok_verify)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "def clamp(value, low, high):\n    return max(low, min(value, high))\n",
            )
        self.assertEqual(out["status"], "COMMITTED")
        self.assertTrue(out["eligible_commit"])
        self.assertTrue(out["transaction_id"])
        self.assertFalse(out["commit"])
        self.assertFalse(out["push"])
        self.assertFalse(out["git_push_ok"])
        print("PASS apply ready handoff is eligible and does not push")

    def test_failed_verify_rolls_back(self):
        calls = []

        def verify(*_args, **_kwargs):
            calls.append(1)
            ok = len(calls) != 2
            return {
                "process_exit_code": 0 if ok else 1,
                "result": {"ok": ok},
                "stdout": "",
                "stderr": "",
            }

        with tempfile.TemporaryDirectory() as tmp:
            project, target = _project(Path(tmp))
            handoff = self.result.prepare_mutation(_payload(), project, TIP)
            out = self.result.apply_ready_handoff(handoff, project, verify=verify)
            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")
        self.assertEqual(out["status"], "ROLLED_BACK")
        self.assertFalse(out["eligible_commit"])
        self.assertFalse(out["applied"])
        self.assertFalse(out["git_push_ok"])
        self.assertNotIn("GIT_PUSH_OK", json.dumps(out))
        print("PASS apply failed verify rolls back without GIT_PUSH_OK")


if __name__ == "__main__":
    unittest.main()
