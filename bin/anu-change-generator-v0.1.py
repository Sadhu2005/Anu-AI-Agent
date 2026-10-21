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
    """Validate changes JSON schema and path safety."""
    if not isinstance(doc, dict):
        raise ValueError("changes root must be an object")
    files = doc.get("files")
    if files is None and "changes" in doc:
        files = doc["changes"]
    if not isinstance(files, list) or not files:
        raise ValueError("changes.files must be a non-empty list")
    if len(files) > MAX_FILES:
        raise ValueError(f"too many files: {len(files)} > {MAX_FILES}")
    out = []
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("each file entry must be an object")
        path = entry.get("path") or entry.get("file")
        if not path or not isinstance(path, str):
            raise ValueError("file entry missing path")
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
        action = (entry.get("action") or entry.get("op") or "modify").lower()
        if action not in ("create", "modify", "delete", "write", "update"):
            raise ValueError(f"unsupported action: {action}")
        content = entry.get("content")
        if action != "delete":
            if content is None:
                raise ValueError(f"content required for {action}: {path}")
            lines = str(content).count("\n") + 1
            if lines > MAX_LINES_PER_FILE:
                raise ValueError(
                    f"file too large: {path} has {lines} lines > {MAX_LINES_PER_FILE}"
                )
        if project_root is not None:
            target = (Path(project_root) / norm).resolve()
            try:
                target.relative_to(Path(project_root).resolve())
            except ValueError as exc:
                raise ValueError(f"path outside project: {path}") from exc
        out.append({
            "path": norm,
            "action": "delete" if action == "delete" else (
                "create" if action == "create" else "modify"
            ),
            "content": None if action == "delete" else content,
        })
    return {"ok": True, "files": out}


def write_changes_file(project_key, doc, *, filename=DEFAULT_CHANGES, root=None):
    validated = validate_changes(doc)
    project = Path(root) if root else (WORKSPACE / project_key)
    project.mkdir(parents=True, exist_ok=True)
    path = project / filename
    payload = {
        "files": [
            {
                "path": f["path"],
                "action": f["action"],
                "content": f["content"],
            }
            for f in validated["files"]
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(path), "file_count": len(payload["files"])}


def generate_todo_list_changes(project_key):
    """Deterministic fallback generator for the classic todo-list goal."""
    doc = {
        "files": [
            {
                "path": "todo.py",
                "action": "create",
                "content": (
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
                ),
            },
            {
                "path": "test_todo.py",
                "action": "create",
                "content": (
                    "import unittest\n"
                    "from todo import TodoList\n\n"
                    "class TestTodo(unittest.TestCase):\n"
                    "    def test_add_and_list(self):\n"
                    "        t = TodoList()\n"
                    "        t.add('a')\n"
                    "        self.assertEqual(t.list(), ['a'])\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                ),
            },
        ]
    }
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
