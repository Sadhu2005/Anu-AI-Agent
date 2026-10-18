#!/usr/bin/env python3
"""F3: project and file path boundary validation."""

import importlib.util
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "bin" / "anu-project-paths-v0.1.py"


def load():
    spec = importlib.util.spec_from_file_location("paths_f3_v062", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def expect_invalid(paths_mod, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except paths_mod.PathValidationError:
        return
    raise AssertionError(f"expected PathValidationError for {fn.__name__}{args!r}")


def test_negative_project_names():
    mod = load()
    for name in (".", "..", "../x", ".git", "bad/name", r"bad\name"):
        expect_invalid(mod, mod.validate_project_name, name)
    print("PASS negative project name validation")


def test_negative_resolve_file():
    mod = load()
    with tempfile.TemporaryDirectory() as workspace:
        project = Path(workspace) / "demo-proj"
        project.mkdir()
        (project / "ok.txt").write_text("ok", encoding="utf-8")
        for rel in (".git/config", "foo/.git/bar", "..", "../secret.txt"):
            expect_invalid(mod, mod.resolve_file, project, rel)
    print("PASS negative resolve_file paths")


def test_negative_resolve_changes_file_absolute_outside():
    mod = load()
    with tempfile.TemporaryDirectory() as workspace:
        project = Path(workspace) / "demo-proj"
        project.mkdir()
        (project / "changes.json").write_text("{}", encoding="utf-8")
        outside = Path(workspace) / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        expect_invalid(mod, mod.resolve_changes_file, project, str(outside))
    print("PASS negative resolve_changes_file outside project")


def test_positive_relative_inside_project():
    mod = load()
    with tempfile.TemporaryDirectory() as workspace:
        project = Path(workspace) / "demo-proj"
        project.mkdir()
        nested = project / "docs"
        nested.mkdir()
        target = nested / "notes.txt"
        target.write_text("notes", encoding="utf-8")
        resolved = mod.resolve_file(project, "docs/notes.txt")
        assert resolved == target.resolve()
        (project / "changes.json").write_text("{}", encoding="utf-8")
        changes = mod.resolve_changes_file(project, "changes.json")
        assert changes == (project / "changes.json").resolve()
    print("PASS positive relative paths inside project")


if __name__ == "__main__":
    print("===== ANU F3 PROJECT PATHS BOUNDARY REGRESSION =====")
    test_negative_project_names()
    test_negative_resolve_file()
    test_negative_resolve_changes_file_absolute_outside()
    test_positive_relative_inside_project()
    print("PROJECT_PATHS_BOUNDARY_TEST_COMPLETE")
