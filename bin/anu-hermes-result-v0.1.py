#!/usr/bin/env python3
"""ANU Hermes result v0.1 — validate a job payload. Does not apply it."""

from __future__ import annotations

from pathlib import Path


def assert_relative_path(workspace: Path, rel: str) -> Path:
    """Reject absolute paths, '..', and symlinks that leave the workspace."""
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("empty path")
    text = rel.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        raise ValueError(f"absolute path: {rel}")
    parts = [part for part in text.split("/") if part != ""]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError(f"path escapes workspace: {rel}")
    root = Path(workspace).resolve()
    cursor = Path(workspace)
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            resolved = cursor.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"symlink escapes workspace: {rel}") from exc
    target = cursor.resolve() if cursor.exists() else root.joinpath(*parts)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {rel}") from exc
    return target


def normalize_result(payload: dict, workspace: Path) -> dict:
    """Force commit and push false. Return file/old/new entries, unapplied."""
    if not isinstance(payload, dict):
        raise ValueError("result payload must be an object")
    changed = payload.get("changed_files") or []
    if not isinstance(changed, list):
        raise ValueError("changed_files must be a list")
    for rel in changed:
        assert_relative_path(workspace, rel)
    files_in = payload.get("files") or []
    if not isinstance(files_in, list):
        raise ValueError("files must be a list")
    files = []
    for entry in files_in:
        if not isinstance(entry, dict):
            raise ValueError("file entry must be an object")
        rel = entry.get("file")
        assert_relative_path(workspace, rel)
        new = entry.get("new")
        if new is None:
            new = entry.get("content") or ""
        old = entry.get("old") or ""
        if not isinstance(new, str) or not isinstance(old, str):
            raise ValueError("file old/new must be strings")
        files.append({"file": rel.strip().replace("\\", "/"), "old": old, "new": new})
    status = payload.get("status") or "failed"
    if status not in {"completed", "failed"}:
        status = "failed"
    return {
        "ok": status == "completed",
        "job_id": payload.get("job_id") or "",
        "status": status,
        "changed_files": [str(rel).replace("\\", "/") for rel in changed],
        "files": files,
        "base_commit": payload.get("base_commit") or "",
        "logs": payload.get("logs") or "",
        "errors": payload.get("errors") or "",
        "test_summary": payload.get("test_summary") or {},
        "commit": False,
        "push": False,
    }
