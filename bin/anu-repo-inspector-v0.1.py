#!/usr/bin/env python3
"""ANU Repo Inspector v0.1 (V0.9.3) — language, tests, entry points."""

from __future__ import annotations

import json
import os
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
WORKSPACE = BASE / "workspace" / "projects"


def inspect_project(project_key, *, root=None):
    root = Path(root) if root else (WORKSPACE / project_key)
    if not root.is_dir():
        return {"ok": False, "error": f"missing project root: {root}"}
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        if any(part.startswith(".git") for part in Path(rel).parts):
            continue
        if len(files) >= 200:
            break
        files.append(rel)
    languages = set()
    for rel in files:
        if rel.endswith(".py"):
            languages.add("python")
        elif rel.endswith((".js", ".ts", ".tsx")):
            languages.add("javascript")
        elif rel.endswith(".go"):
            languages.add("go")
    test_cmd = detect_test_command(root)
    entry_points = [
        f for f in files
        if f in ("app.py", "main.py", "index.js", "src/main.py", "src/index.ts")
        or f.endswith("/app.py")
    ]
    # Empty lab: only .git / README / ignore scaffolding.
    meaningful = [
        f for f in files
        if not f.startswith(".git")
        and f not in (".gitignore", "LICENSE", "README.md", "AGENTS.md")
        and not f.startswith(".github/")
    ]
    summary = {
        "ok": True,
        "project_key": project_key,
        "root": str(root),
        "file_count": len(files),
        "files_sample": files[:40],
        "languages": sorted(languages),
        "test_command": test_cmd,
        "entry_points": entry_points,
        "has_git": (root / ".git").exists(),
        "empty_lab": len(meaningful) == 0,
        "meaningful_file_count": len(meaningful),
    }
    return summary


def detect_test_command(root: Path) -> dict:
    root = Path(root)
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
        # Prefer pytest when config present; still OK if pytest missing at runtime.
        return {"argv": ["python", "-m", "pytest", "-q"], "kind": "pytest"}
    if any(root.glob("test_*.py")) or any(root.glob("*_test.py")) or (root / "tests").is_dir():
        if any((root / "tests").glob("test_*.py")) if (root / "tests").is_dir() else False:
            return {"argv": ["python", "-m", "unittest", "discover", "-s", "tests", "-q"], "kind": "unittest"}
        return {"argv": ["python", "-m", "unittest", "discover", "-q"], "kind": "unittest"}
    if (root / "package.json").is_file():
        return {"argv": ["npm", "test", "--silent"], "kind": "npm"}
    # Fallback: compile all python files
    return {"argv": ["python", "-m", "compileall", "-q", "."], "kind": "py_compile"}


def store_summary_in_memory(db, goal_id, summary):
    try:
        import importlib.util
        path = None
        for candidate in (
            BASE / "bin" / "anu-llm-router-v0.1.py",
            Path(__file__).resolve().parent / "anu-llm-router-v0.1.py",
        ):
            if candidate.is_file():
                path = candidate
                break
        if path is None:
            return False
        spec = importlib.util.spec_from_file_location("anu_llm_insp", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.store_context(
            db,
            goal_id=goal_id,
            task_id=None,
            role="system",
            content="Repo summary:\n" + json.dumps(summary, indent=2)[:4000],
            summary="repo_inspector",
            provider="repo_inspector",
        )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    args = p.parse_args()
    print(json.dumps(inspect_project(args.project), indent=2))
