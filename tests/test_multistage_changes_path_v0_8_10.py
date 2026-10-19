#!/usr/bin/env python3
"""V0.8.10: project-relative changes file for multi-stage mutation stages."""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PATHS = REPO / "bin" / "anu-project-paths-v0.1.py"
TPL = REPO / "bin" / "anu-template-planner-v0.1.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_and_candidates():
    paths = load(PATHS, "paths_0810")
    assert paths.DEFAULT_CHANGES_FILE == ".anu-changes.json"
    assert ".anu-v0.5.9-changes.json" in paths.CHANGES_FILE_CANDIDATES
    print("PASS default and candidates")


def test_fallback_from_bare_changes_json():
    paths = load(PATHS, "paths_0810b")
    with tempfile.TemporaryDirectory() as td:
        project = Path(td) / "sandbox-test-project"
        project.mkdir()
        legacy = project / ".anu-v0.5.9-changes.json"
        legacy.write_text(
            '[{"file":"app.py","old":"a","new":"b"}]', encoding="utf-8"
        )
        resolved = paths.resolve_changes_file(project, "changes.json")
        assert resolved == legacy.resolve()
        picked = paths.pick_existing_changes_file(project, preferred="changes.json")
        assert picked == ".anu-v0.5.9-changes.json"
    print("PASS fallback from bare changes.json")


def test_explicit_missing_does_not_fallback():
    paths = load(PATHS, "paths_0810c")
    with tempfile.TemporaryDirectory() as td:
        project = Path(td) / "sandbox-test-project"
        project.mkdir()
        (project / ".anu-changes.json").write_text("[]", encoding="utf-8")
        try:
            paths.resolve_changes_file(project, "my-custom-plan.json")
        except paths.PathValidationError:
            pass
        else:
            raise AssertionError("expected PathValidationError for explicit miss")
    print("PASS explicit missing does not fallback")


def test_prefer_canonical_when_present():
    paths = load(PATHS, "paths_0810d")
    with tempfile.TemporaryDirectory() as td:
        project = Path(td) / "sandbox-test-project"
        project.mkdir()
        canonical = project / ".anu-changes.json"
        canonical.write_text("[]", encoding="utf-8")
        (project / ".anu-v0.5.9-changes.json").write_text("[]", encoding="utf-8")
        resolved = paths.resolve_changes_file(project, "changes.json")
        assert resolved == canonical.resolve()
    print("PASS prefer canonical .anu-changes.json")


def test_planner_normalize_from_workspace():
    planner = load(TPL, "tpl_0810")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        project = base / "workspace" / "projects" / "sandbox-test-project"
        project.mkdir(parents=True)
        (project / ".anu-v0.5.9-changes.json").write_text("[]", encoding="utf-8")
        planner.BASE = base
        assert planner.DEFAULT_CHANGES_FILE == ".anu-changes.json"
        got = planner._normalize_changes_file_for_expand(
            "sandbox-test-project", "changes.json"
        )
        assert got == ".anu-v0.5.9-changes.json", got
        # Without a live tree, bare changes.json becomes canonical default.
        planner.BASE = base / "missing"
        assert (
            planner._normalize_changes_file_for_expand(
                "sandbox-test-project", "changes.json"
            )
            == ".anu-changes.json"
        )
    print("PASS planner normalize from workspace")


def main():
    test_default_and_candidates()
    test_fallback_from_bare_changes_json()
    test_explicit_missing_does_not_fallback()
    test_prefer_canonical_when_present()
    test_planner_normalize_from_workspace()
    print("ALL_PASS test_multistage_changes_path_v0_8_10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
