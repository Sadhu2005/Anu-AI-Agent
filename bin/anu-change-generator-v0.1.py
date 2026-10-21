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


def generate_todo_list_changes(project_key):
    """Deterministic fallback generator for the classic todo-list goal."""
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
    doc = [
        {"file": "todo.py", "old": "# ANU_STUB_todo.py\n", "new": todo},
        {"file": "test_todo.py", "old": "# ANU_STUB_test_todo.py\n", "new": test},
    ]
    return write_changes_file(project_key, doc)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--todo-demo", action="store_true")
    args = p.parse_args()
    if args.todo_demo:
        print(json.dumps(generate_todo_list_changes(args.project), indent=2))
    else:
        print(json.dumps({"ok": False, "error": "provide --todo-demo or use as library"}))
