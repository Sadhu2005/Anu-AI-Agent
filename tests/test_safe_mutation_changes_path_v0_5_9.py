import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


MODULE_PATH = Path("/srv/anu/bin/anu-safe-mutation-v0.2.py")


def load_module():
    spec = importlib.util.spec_from_file_location(
        "anu_safe_mutation_v0_2_path_test",
        MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_main_resolves_project_relative_changes_file():
    module = load_module()

    with TemporaryDirectory() as td:
        workspace = Path(td) / "workspace"
        project = workspace / "sandbox-test-project"
        project.mkdir(parents=True)

        changes_file = project / ".anu-v0.5.9-changes.json"
        changes_file.write_text(
            '[{"file":"app.py","old":"old","new":"new"}]',
            encoding="utf-8",
        )

        module.WORKSPACE = workspace

        captured = {}

        class StopAfterCapture(Exception):
            pass

        original_ensure_state = module.ensure_state
        original_load_changes = module.load_changes

        def fake_ensure_state():
            return None

        def fake_load_changes(path):
            captured["path"] = Path(path)
            raise StopAfterCapture()

        module.ensure_state = fake_ensure_state
        module.load_changes = fake_load_changes

        original_argv = sys.argv
        try:
            sys.argv = [
                str(MODULE_PATH),
                "--project",
                "sandbox-test-project",
                "--changes-file",
                ".anu-v0.5.9-changes.json",
                "--task-id",
                "186",
                "--operation",
                "python_test",
            ]

            try:
                module.main()
            except StopAfterCapture:
                pass
        finally:
            sys.argv = original_argv
            module.ensure_state = original_ensure_state
            module.load_changes = original_load_changes

        assert captured["path"] == changes_file.resolve()

        loaded = module.load_changes(captured["path"])
        assert loaded[0]["file"] == "app.py"

        # Security boundaries must remain intact.
        for bad_path in (
            str(changes_file.resolve()),
            "../outside.json",
            "nested/../../outside.json",
        ):
            try:
                module.resolve_file(project, bad_path)
            except SystemExit:
                pass
            else:
                raise AssertionError(
                    f"resolve_file accepted unsafe path: {bad_path}"
                )


def test_changes_file_contract():
    module = load_module()

    with TemporaryDirectory() as td:
        project = Path(td) / "project"
        project.mkdir()
        inside = project / ".changes.json"
        inside.write_text("[]", encoding="utf-8")
        outside = Path(td) / "outside.json"
        outside.write_text("[]", encoding="utf-8")

        # Relative changes files resolve inside the project.
        assert module.resolve_changes_file(project, ".changes.json") == inside.resolve()

        # Absolute changes files keep the pre-V0.5.9 contract.
        assert module.resolve_changes_file(project, str(outside)) == outside

        # Relative paths must not escape the project.
        for bad_path in ("", "../outside.json", "nested/../../outside.json"):
            try:
                module.resolve_changes_file(project, bad_path)
            except SystemExit:
                pass
            else:
                raise AssertionError(
                    f"resolve_changes_file accepted unsafe path: {bad_path!r}"
                )


if __name__ == "__main__":
    test_main_resolves_project_relative_changes_file()
    test_changes_file_contract()
    print("SAFE_MUTATION_CHANGES_PATH_V0_5_9_PASS")
