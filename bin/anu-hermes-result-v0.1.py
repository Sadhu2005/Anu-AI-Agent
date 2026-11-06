#!/usr/bin/env python3
"""ANU Hermes result v0.1 — validate a job payload. Does not apply it."""

from __future__ import annotations

import re
from pathlib import Path


# Same prefixes and secret shapes as config/anu-autonomy.yaml policy.
PROTECTED_PREFIXES = (
    ".github/workflows/",
    "deploy/",
    "infra/",
)
SECRET_PATTERNS = (
    r"(?i)api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}",
    r"(?i)secret\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}",
    r"(?i)BEGIN (RSA |OPENSSH )?PRIVATE KEY",
    r"ghp_[A-Za-z0-9]{20,}",
    r"sk-[A-Za-z0-9]{20,}",
)
MAX_FILES = 20
MAX_FILE_BYTES = 64 * 1024


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


def _display_path(rel: str) -> str:
    text = rel.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _secret_path(rel: str) -> bool:
    parts = _display_path(rel).split("/")
    if "secrets" in parts:
        return True
    name = parts[-1]
    return name == ".env" or name.startswith(".env.") or name == "id_rsa" or name.endswith(".pem")


def _protected_path(rel: str) -> bool:
    text = _display_path(rel)
    for prefix in PROTECTED_PREFIXES:
        if text.startswith(prefix) or f"/{prefix}" in f"/{text}":
            return True
    return False


def _secret_text(text: str) -> bool:
    for pattern in SECRET_PATTERNS:
        if re.search(pattern, text or ""):
            return True
    return False


def _abort(reason: str, job_id: str = "") -> dict:
    return {
        "ok": False,
        "applied": False,
        "commit": False,
        "push": False,
        "job_id": job_id,
        "base_commit": "",
        "changes": [],
        "reason": reason,
    }


def prepare_mutation(payload: dict, workspace: Path, project_tip: str) -> dict:
    """Turn a finished Hermes result into file/old/new. Never writes or applies."""
    job_id = payload.get("job_id") if isinstance(payload, dict) else ""
    job_id = job_id or ""
    try:
        result = normalize_result(payload, workspace)
    except (ValueError, TypeError, AttributeError) as exc:
        return _abort(str(exc), job_id)
    job_id = result["job_id"]
    if result["status"] != "completed":
        return _abort(f"status {result['status']}", job_id)
    tip = (project_tip or "").strip()
    base = (result["base_commit"] or "").strip()
    if not tip or base != tip:
        return _abort("base_commit does not match project tip", job_id)
    files = result["files"]
    changed = [_display_path(path) for path in result["changed_files"]]
    listed = [_display_path(item["file"]) for item in files]
    if not files or set(listed) != set(changed) or len(listed) != len(set(listed)):
        return _abort("changed_files do not match files", job_id)
    if len(files) > MAX_FILES:
        return _abort("too many files", job_id)
    changes = []
    for item in files:
        rel = _display_path(item["file"])
        old = item["old"]
        new = item["new"]
        if _secret_path(rel):
            return _abort(f"secret path: {rel}", job_id)
        if _protected_path(rel):
            return _abort(f"protected path: {rel}", job_id)
        if new == "" and old != "":
            return _abort(f"delete rejected: {rel}", job_id)
        if len(old.encode("utf-8")) + len(new.encode("utf-8")) > MAX_FILE_BYTES:
            return _abort(f"file too large: {rel}", job_id)
        if _secret_text(old + "\n" + new):
            return _abort(f"secret content: {rel}", job_id)
        changes.append({"file": rel, "old": old, "new": new})
    return {
        "ok": True,
        "applied": False,
        "commit": False,
        "push": False,
        "job_id": job_id,
        "base_commit": base,
        "changes": changes,
        "reason": "ready",
    }
