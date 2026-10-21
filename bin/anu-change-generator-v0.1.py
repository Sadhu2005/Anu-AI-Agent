#!/usr/bin/env python3
"""ANU Change Generator v0.1 (V0.9.3) — validate / limit .anu-changes.json."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
WORKSPACE = BASE / "workspace" / "projects"
DEFAULT_CHANGES = ".anu-changes.json"

MAX_FILES = 20
MAX_LINES_PER_FILE = 400
# Soft preference for multi-file implement stages (still capped by MAX_FILES).
PREFERRED_IMPLEMENT_FILES = 3

IMPLEMENT_ROLES = frozenset({"implement", "fix", "apply", "propose"})
VERIFY_ROLES = frozenset({
    "inspect", "test", "review", "verify", "design", "research", "identify",
    "architecture", "bootstrap", "env",
})

STAGE_PROMPTS = {
    "inspect": "Survey existing files; prefer verify-only or noop changes.",
    "implement": "Implement a small reviewable change; prefer 1–3 related files.",
    "fix": "Repair the failing layer using DIAGNOSIS.md targets; minimal diff.",
    "test": "Add or adjust tests only; do not change production behavior unless required.",
    "review": "Verify-only preferred; document gaps in evidence text.",
    "verify": "Verify-only; confirm prior commit digests.",
    "bootstrap": "Add missing deps/config scaffolding only.",
    "architecture": "Docs/architecture notes only (no production code unless asked).",
}


def stage_prompt(role: str) -> str:
    key = str(role or "").strip().lower()
    return STAGE_PROMPTS.get(key, STAGE_PROMPTS["implement"])

_PLACEHOLDER_GOAL = re.compile(r"^[\s.…·•\-_]*$")
# Match "todo", "TodoList", "todo_list", "to-do", etc. (\\btodo\\b misses TodoList).
_TODO_HINT = re.compile(
    r"(?i)(?:\btodo\b|todolist|todo[_\s-]?list|to-dos?|checklist|task[_\s-]?list)"
)
_PING_HINT = re.compile(r"\bping\b", re.I)
_GREET_HINT = re.compile(r"\bgreet\b", re.I)


def validate_changes(doc, *, project_root=None):
    """Validate Safe Mutation changes: a JSON array of {file, old, new}.

    Also accepts a legacy object form ``{"files":[{path,action,content}]}``
    and normalizes it to the engine contract.
    """
    # Engine contract: top-level array
    if isinstance(doc, list):
        files = doc
        legacy = False
    elif isinstance(doc, dict):
        files = doc.get("files")
        if files is None and "changes" in doc:
            files = doc["changes"]
        legacy = True
    else:
        raise ValueError("changes root must be a list or object")
    if not isinstance(files, list) or not files:
        raise ValueError("changes must be a non-empty list")
    if len(files) > MAX_FILES:
        raise ValueError(f"too many files: {len(files)} > {MAX_FILES}")
    out = []
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("each file entry must be an object")
        # Prefer Safe Mutation keys; fall back to legacy path/content.
        if "file" in entry and "old" in entry and "new" in entry:
            path = entry["file"]
            old = entry["old"]
            new = entry["new"]
            if not isinstance(path, str) or not isinstance(old, str) or not isinstance(new, str):
                raise ValueError("file/old/new must be strings")
        elif legacy:
            path = entry.get("path") or entry.get("file")
            if not path or not isinstance(path, str):
                raise ValueError("file entry missing path")
            action = (entry.get("action") or entry.get("op") or "modify").lower()
            content = entry.get("content")
            if action == "delete":
                raise ValueError("delete not supported by Safe Mutation v0.2")
            if content is None or not isinstance(content, str):
                raise ValueError(f"content required for {path}")
            old = entry.get("old") or f"# ANU_STUB_{Path(path).name}\n"
            new = content
        else:
            raise ValueError("each change requires file/old/new strings")
        norm = path.replace("\\", "/")
        while norm.startswith("./"):
            norm = norm[2:]
        if (
            norm.startswith("/")
            or norm.startswith("../")
            or "/../" in f"/{norm}/"
            or norm == ".."
            or ".." in norm.split("/")
        ):
            raise ValueError(f"path escapes workspace: {path}")
        lines = new.count("\n") + 1
        if lines > MAX_LINES_PER_FILE:
            raise ValueError(
                f"file too large: {norm} has {lines} lines > {MAX_LINES_PER_FILE}"
            )
        if project_root is not None:
            target = (Path(project_root) / norm).resolve()
            try:
                target.relative_to(Path(project_root).resolve())
            except ValueError as exc:
                raise ValueError(f"path outside project: {path}") from exc
        out.append({"file": norm, "old": old, "new": new})
    return {"ok": True, "files": out}


def write_changes_file(project_key, doc, *, filename=DEFAULT_CHANGES, root=None):
    """Write Safe Mutation array format; seed stub files when old is a stub marker."""
    project = Path(root) if root else (WORKSPACE / project_key)
    project.mkdir(parents=True, exist_ok=True)
    validated = validate_changes(doc, project_root=project)
    # Seed stubs so create-via-replace works (engine requires file + 1 match).
    # Never overwrite an existing file with a stub — that would destroy work.
    for entry in validated["files"]:
        target = project / entry["file"]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry["old"], encoding="utf-8")
    path = project / filename
    payload = [
        {"file": f["file"], "old": f["old"], "new": f["new"]}
        for f in validated["files"]
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(path), "file_count": len(payload)}


def _todo_sources():
    todo = (
        "#!/usr/bin/env python3\n"
        "\"\"\"Simple in-memory todo list.\"\"\"\n\n"
        "class TodoList:\n"
        "    def __init__(self):\n"
        "        self._items = []\n\n"
        "    def add(self, text):\n"
        "        text = str(text).strip()\n"
        "        if not text:\n"
        "            raise ValueError('empty todo')\n"
        "        self._items.append(text)\n"
        "        return text\n\n"
        "    def list(self):\n"
        "        return list(self._items)\n\n"
        "    def clear(self):\n"
        "        self._items.clear()\n"
    )
    test = (
        "import unittest\n"
        "from todo import TodoList\n\n"
        "class TestTodo(unittest.TestCase):\n"
        "    def test_add_and_list(self):\n"
        "        t = TodoList()\n"
        "        t.add('a')\n"
        "        self.assertEqual(t.list(), ['a'])\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    return todo, test


def _entry_for_desired(project: Path, rel: str, desired: str) -> dict:
    """Build {file,old,new} that applies cleanly against the current tree.

    - Missing file → seed stub as old, new = desired
    - Already matches desired → noop (old == new == current)
    - Differs → full-file replace (old = current, new = desired)
    """
    target = project / rel
    stub = f"# ANU_STUB_{Path(rel).name}\n"
    if not target.exists():
        return {"file": rel, "old": stub, "new": desired}
    current = target.read_text(encoding="utf-8")
    if current == desired:
        return {"file": rel, "old": current, "new": current}
    return {"file": rel, "old": current, "new": desired}


def ensure_noop_changes(project_key, *, filename=DEFAULT_CHANGES, root=None):
    """Write identity changes so inspect/test/verify stages can COMMIT."""
    project = Path(root) if root else (WORKSPACE / project_key)
    project.mkdir(parents=True, exist_ok=True)
    # Prefer real code modules over README so lab repos (src/labapp) verify cleanly.
    candidates = [
        "src/labapp/__init__.py",
        "labapp/__init__.py",
        "app.py",
        "todo.py",
        "main.py",
        "README.md",
    ]
    chosen = None
    for name in candidates:
        path = project / name
        if path.is_file():
            chosen = name
            break
    if chosen is None:
        # Create a tiny marker the mutation engine can "apply" as a noop.
        chosen = ".anu-verify-marker"
        marker = project / chosen
        if not marker.exists():
            marker.write_text("# anu verify marker\n", encoding="utf-8")
    text = (project / chosen).read_text(encoding="utf-8")
    doc = [{"file": chosen, "old": text, "new": text}]
    result = write_changes_file(project_key, doc, filename=filename, root=project)
    result["mode"] = "noop"
    result["file"] = chosen
    return result


def generate_todo_list_changes(project_key, *, filename=DEFAULT_CHANGES, root=None):
    """Deterministic fallback generator for the classic todo-list goal.

    Idempotent: re-running against an already-implemented tree writes noops
    (or full-file replaces if content drifted), never stale stub ``old`` values.
    """
    project = Path(root) if root else (WORKSPACE / project_key)
    todo, test = _todo_sources()
    doc = [
        _entry_for_desired(project, "todo.py", todo),
        _entry_for_desired(project, "test_todo.py", test),
    ]
    result = write_changes_file(project_key, doc, filename=filename, root=project)
    result["mode"] = (
        "noop" if all(e["old"] == e["new"] for e in doc) else "apply"
    )
    return result


def _ping_sources():
    mod = (
        '"""Lab ping helper for ANU agent/* demos."""\n\n'
        "\n"
        "def ping() -> str:\n"
        '    """Return a fixed pong token."""\n'
        '    return "pong"\n'
    )
    test = (
        "import unittest\n\n"
        "from labapp.ping import ping\n\n"
        "\n"
        "class TestPing(unittest.TestCase):\n"
        "    def test_ping_returns_pong(self):\n"
        '        self.assertEqual(ping(), "pong")\n\n'
        "\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    return mod, test


def _greet_sources():
    init = (
        '"""Minimal lab application package for ANU experiments."""\n\n'
        '__version__ = "0.1.0"\n\n'
        "\n"
        'def greet(name: str = "world") -> str:\n'
        '    """Return a greeting — seed unit for the test matrix."""\n'
        '    return f"Hello, {name}!"\n'
    )
    test = (
        "import unittest\n\n"
        "from labapp import greet\n\n"
        "\n"
        "class TestGreet(unittest.TestCase):\n"
        "    def test_greet_default(self):\n"
        '        self.assertEqual(greet(), "Hello, world!")\n\n'
        "    def test_greet_name(self):\n"
        '        self.assertEqual(greet("Ada"), "Hello, Ada!")\n\n'
        "\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    return init, test


def generate_ping_changes(project_key, *, filename=DEFAULT_CHANGES, root=None):
    """Deterministic ping() helper + unit test under src/labapp."""
    project = Path(root) if root else (WORKSPACE / project_key)
    mod, test = _ping_sources()
    doc = [
        _entry_for_desired(project, "src/labapp/ping.py", mod),
        _entry_for_desired(project, "tests/unit/test_ping.py", test),
    ]
    # Keep package export discoverable when __init__ exists.
    init_path = project / "src/labapp/__init__.py"
    if init_path.is_file():
        current = init_path.read_text(encoding="utf-8")
        if "ping" not in current:
            desired = current.rstrip() + "\n\nfrom .ping import ping  # noqa: F401\n"
            doc.append(_entry_for_desired(project, "src/labapp/__init__.py", desired))
    result = write_changes_file(project_key, doc, filename=filename, root=project)
    result["mode"] = (
        "noop" if all(e["old"] == e["new"] for e in doc) else "apply"
    )
    result["helper"] = "ping"
    return result


def generate_greet_changes(project_key, *, filename=DEFAULT_CHANGES, root=None):
    """Deterministic greet() in labapp + unit test."""
    project = Path(root) if root else (WORKSPACE / project_key)
    init, test = _greet_sources()
    # Preserve ping export if a prior lab goal added it.
    init_path = project / "src/labapp/__init__.py"
    if init_path.is_file() and "ping" in init_path.read_text(encoding="utf-8"):
        if "from .ping import ping" not in init:
            init = init.rstrip() + "\n\nfrom .ping import ping  # noqa: F401\n"
    functional = (
        '"""Functional: greet behaves as a stable public API."""\n\n'
        "import unittest\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[2] / \"src\"))\n\n"
        "from labapp import greet\n\n"
        "\n"
        "class TestGreetFunctional(unittest.TestCase):\n"
        "    def test_api_contract(self):\n"
        '        out = greet("lab")\n'
        '        self.assertTrue(out.startswith("Hello,"))\n'
        '        self.assertIn("lab", out)\n'
        '        self.assertTrue(out.endswith("!"))\n\n'
        "\n"
        'if __name__ == "__main__":\n'
        "    unittest.main()\n"
    )
    e2e = (
        '"""E2E smoke for labapp package."""\n\n'
        "import unittest\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[2] / \"src\"))\n\n"
        "from labapp import greet\n\n"
        "\n"
        "class TestE2ESmoke(unittest.TestCase):\n"
        "    def test_package_smoke(self):\n"
        '        self.assertEqual(greet("e2e"), "Hello, e2e!")\n\n'
        "\n"
        'if __name__ == "__main__":\n'
        "    unittest.main()\n"
    )
    regression = (
        '"""Regression: prior baseline greetings must stay stable."""\n\n'
        "import unittest\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[2] / \"src\"))\n\n"
        "from labapp import greet\n\n"
        "\n"
        "class TestRegressionBaseline(unittest.TestCase):\n"
        "    def test_world_still_works(self):\n"
        '        self.assertEqual(greet(), "Hello, world!")\n\n'
        "\n"
        'if __name__ == "__main__":\n'
        "    unittest.main()\n"
    )
    doc = [
        _entry_for_desired(project, "src/labapp/__init__.py", init),
        _entry_for_desired(project, "tests/unit/test_greet.py", test),
        _entry_for_desired(project, "tests/functional/test_greet_api.py", functional),
        _entry_for_desired(project, "tests/e2e/test_smoke.py", e2e),
        _entry_for_desired(project, "tests/regression/test_baseline.py", regression),
    ]
    result = write_changes_file(project_key, doc, filename=filename, root=project)
    result["mode"] = (
        "noop" if all(e["old"] == e["new"] for e in doc) else "apply"
    )
    result["helper"] = "greet"
    return result


def wants_ping_goal(goal_description: str | None) -> bool:
    return bool(_PING_HINT.search((goal_description or "").strip()))


def wants_greet_goal(goal_description: str | None) -> bool:
    text = (goal_description or "").strip()
    return bool(_GREET_HINT.search(text)) and not wants_ping_goal(text)


def role_from_task_title(title: str | None) -> str:
    """Extract stage role from titles like ``software_goal:implement``."""
    if not title or not isinstance(title, str):
        return ""
    if ":" in title:
        return title.rsplit(":", 1)[-1].strip().lower()
    return title.strip().lower()


def wants_todo_goal(goal_description: str | None) -> bool:
    text = (goal_description or "").strip()
    if not text:
        return False
    return bool(_TODO_HINT.search(text))


def is_placeholder_goal(description: str | None) -> bool:
    text = (description or "").strip()
    if not text:
        return True
    if _PLACEHOLDER_GOAL.match(text):
        return True
    if text in {"...", "…", "TBD", "tbd", "TODO", "todo"}:
        return True
    return False


def prepare_for_task(
    project_key,
    role,
    goal_description="",
    *,
    filename=DEFAULT_CHANGES,
    root=None,
):
    """Rewrite the changes file for the upcoming stage role.

    Inspect/test/review/verify → noop against current tree.
    Implement (todo / ping / greet lab goals) → deterministic file generators.
    Other implement goals without a generator → noop (verify existing tree).
    """
    role_l = (role or "").strip().lower()
    project = Path(root) if root else (WORKSPACE / project_key)
    if role_l in IMPLEMENT_ROLES:
        # Match goal text first. Do NOT let a leftover todo.py from an older
        # lab run hijack ping/greet (or other) implement goals.
        if wants_ping_goal(goal_description):
            return generate_ping_changes(
                project_key, filename=filename, root=project
            )
        if wants_greet_goal(goal_description):
            return generate_greet_changes(
                project_key, filename=filename, root=project
            )
        todo_path = project / "todo.py"
        if wants_todo_goal(goal_description) or (
            todo_path.is_file() and not (goal_description or "").strip()
        ):
            return generate_todo_list_changes(
                project_key, filename=filename, root=project
            )
        return ensure_noop_changes(project_key, filename=filename, root=project)
    return ensure_noop_changes(project_key, filename=filename, root=project)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--todo-demo", action="store_true")
    p.add_argument("--noop", action="store_true")
    p.add_argument("--role", default="")
    p.add_argument("--goal", default="")
    args = p.parse_args()
    if args.todo_demo:
        print(json.dumps(generate_todo_list_changes(args.project), indent=2))
    elif args.noop:
        print(json.dumps(ensure_noop_changes(args.project), indent=2))
    elif args.role:
        print(json.dumps(
            prepare_for_task(args.project, args.role, args.goal), indent=2
        ))
    else:
        print(json.dumps({
            "ok": False,
            "error": "provide --todo-demo, --noop, or --role",
        }))
