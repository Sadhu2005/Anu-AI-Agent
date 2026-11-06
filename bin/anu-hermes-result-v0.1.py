#!/usr/bin/env python3
"""ANU Hermes result v0.1 — validate a job payload and hand it to Safe Mutation.

prepare_mutation does not write. apply_ready_handoff is the only apply path,
and it refuses anything that is not a ready handoff. It does not commit or push.
"""

from __future__ import annotations

import contextlib
import gc
import importlib.util
import io
import json
import os
import sqlite3
import sys
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


def _under_production(path: Path) -> bool:
    raw = str(path).replace("\\", "/")
    try:
        resolved = str(path.resolve()).replace("\\", "/")
    except OSError:
        resolved = raw
    for text in (raw, resolved):
        if text == "/srv/anu" or text.startswith("/srv/anu/"):
            return True
    return False


def _closed(reason: str) -> dict:
    return {
        "ok": False,
        "applied": False,
        "eligible_commit": False,
        "commit": False,
        "push": False,
        "git_push_ok": False,
        "status": "NOT_APPLIED",
        "transaction_id": "",
        "changes": [],
        "reason": reason,
    }


def _refuse_handoff(handoff) -> str:
    if not isinstance(handoff, dict):
        return "handoff must be an object"
    if handoff.get("commit") or handoff.get("push"):
        return "handoff commit and push must stay false"
    if not handoff.get("ok") or handoff.get("reason") != "ready":
        return str(handoff.get("reason") or "handoff is not ready")
    changes = handoff.get("changes")
    if not isinstance(changes, list) or not changes:
        return "empty changes"
    for item in changes:
        if not isinstance(item, dict):
            return "change must be an object"
        for key in ("file", "old", "new"):
            if not isinstance(item.get(key), str):
                return f"change requires string {key}"
    return ""


_ENGINE_LOADS = 0


def _load_safe_mutation():
    global _ENGINE_LOADS
    _ENGINE_LOADS += 1
    path = Path(__file__).resolve().parent / "anu-safe-mutation-v0.2.py"
    spec = importlib.util.spec_from_file_location(
        f"anu_safe_mutation_handoff_{os.getpid()}_{_ENGINE_LOADS}",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module.os, "fchmod"):
        module.os.fchmod = lambda fd, mode: None
    return module


def _run_safe_mutation(project_dir: Path, changes: list, verify) -> dict:
    """Checkpoint, apply, and verify through the existing Safe Mutation engine."""
    module = _load_safe_mutation()
    state = project_dir.parent.parent / "anu-handoff-state"
    module.DB_FILE = state / "brain.db"
    module.LOG_FILE = state / "logs" / "anu-mutations.jsonl"
    module.CHECKPOINT_ROOT = state / "checkpoints"
    module.WORKSPACE = project_dir.parent
    module.EXECUTION_LOCK_FILE = state / "execution.lock"
    module.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    schema = Path(__file__).resolve().parents[1] / "migrations" / "0001_initial.sql"
    db = sqlite3.connect(module.DB_FILE)
    try:
        db.executescript(schema.read_text(encoding="utf-8"))
        db.commit()
    finally:
        db.close()
    module.run_gateway = verify
    changes_path = project_dir / ".anu-changes.json"
    changes_path.write_text(json.dumps(changes), encoding="utf-8")
    argv = [
        "anu-safe-mutation-v0.2.py",
        "--project",
        project_dir.name,
        "--changes-file",
        str(changes_path),
        "--operation",
        "python_test",
    ]
    saved = sys.argv
    sys.argv = argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            module.main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    else:
        code = 0
    finally:
        sys.argv = saved
    text = buf.getvalue().strip()
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        payload = {"ok": False, "error": text}
    if not isinstance(payload, dict):
        payload = {"ok": False, "error": "engine returned a non-object"}
    payload["exit_code"] = code
    del module
    gc.collect()
    return payload


def apply_ready_handoff(handoff, project_dir, *, verify, engine=None) -> dict:
    """Apply one ready handoff. Aborted handoffs never reach the engine."""
    reason = _refuse_handoff(handoff)
    if reason:
        return _closed(reason)
    project = Path(project_dir)
    if _under_production(project):
        return _closed("production path refused")
    if engine is None:
        if verify is None:
            return _closed("verify is required")
        engine = lambda changes: _run_safe_mutation(project, changes, verify)
    raw = engine(handoff["changes"])
    status = str((raw or {}).get("status") or "MUTATION_ENGINE_ERROR")
    committed = status in {"COMMITTED", "ALREADY_COMMITTED"}
    return {
        "ok": committed,
        "applied": committed,
        "eligible_commit": committed,
        "commit": False,
        "push": False,
        "git_push_ok": False,
        "status": status,
        "transaction_id": str((raw or {}).get("transaction_id") or ""),
        "changes": list(handoff["changes"]) if committed else [],
        "reason": status,
    }
