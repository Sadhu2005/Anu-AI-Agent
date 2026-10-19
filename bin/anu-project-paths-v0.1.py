#!/usr/bin/env python3
"""Shared project / path validators (review F3)."""

from __future__ import annotations

import os
import re
from pathlib import Path


DEFAULT_BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DEFAULT_WORKSPACE = DEFAULT_BASE / "workspace" / "projects"

# Registered-style project directory names only (no ".", "..", slashes).
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# V0.8.10: canonical project-relative mutation plan; prefer this over bare
# ``changes.json`` (which often does not exist in the project tree).
DEFAULT_CHANGES_FILE = ".anu-changes.json"

# Ordered fallbacks when the preferred name is missing (legacy sandbox plans).
CHANGES_FILE_CANDIDATES = (
    DEFAULT_CHANGES_FILE,
    ".anu-v0.5.9-changes.json",
    "changes.json",
)

# Names that may silently fall back to another candidate when missing.
_LOOSE_CHANGES_NAMES = frozenset(CHANGES_FILE_CANDIDATES)


class PathValidationError(ValueError):
    """Raised when a project or file path is rejected."""


def validate_project_name(project_name):
    if not isinstance(project_name, str) or not project_name.strip():
        raise PathValidationError("Invalid project path.")
    name = project_name.strip()
    if name in (".", "..") or "/" in name or "\\" in name:
        raise PathValidationError("Invalid project path.")
    if not PROJECT_NAME_RE.match(name):
        raise PathValidationError(f"Invalid project name: {project_name}")
    return name


def resolve_project(workspace, project_name):
    name = validate_project_name(project_name)
    root = Path(workspace).resolve()
    project = (root / name).resolve()
    try:
        project.relative_to(root)
    except ValueError as exc:
        raise PathValidationError("Project is outside ANU workspace.") from exc
    if not project.is_dir():
        raise PathValidationError(f"Project does not exist: {project_name}")
    return project


def _reject_git_parts(parts):
    for part in parts:
        if part == ".git" or part.startswith(".git"):
            raise PathValidationError(
                f"Target path under .git is not allowed: {part}"
            )


def resolve_file(project, relative_file):
    if not relative_file:
        raise PathValidationError("Invalid target file path.")
    p = Path(relative_file)
    if p.is_absolute() or ".." in p.parts:
        raise PathValidationError(f"Invalid target file path: {relative_file}")
    _reject_git_parts(p.parts)
    target = (Path(project) / p).resolve()
    try:
        target.relative_to(Path(project).resolve())
    except ValueError as exc:
        raise PathValidationError(
            f"Target file is outside project: {relative_file}"
        ) from exc
    _reject_git_parts(target.relative_to(Path(project).resolve()).parts)
    if not target.is_file():
        raise PathValidationError(f"Target file does not exist: {relative_file}")
    return target


def _changes_file_is_loose_default(changes_file):
    """True when missing file may fall back to another project candidate."""
    if not changes_file:
        return False
    p = Path(str(changes_file))
    if p.is_absolute():
        return False
    name = p.name if len(p.parts) == 1 else str(p).replace("\\", "/")
    return name in _LOOSE_CHANGES_NAMES or str(p).replace("\\", "/") in _LOOSE_CHANGES_NAMES


def pick_existing_changes_file(project, preferred=None):
    """Return a relative changes-file path that exists inside ``project``.

    Tries ``preferred`` first (when set), then ``CHANGES_FILE_CANDIDATES``.
    Raises PathValidationError if none exist.
    """
    project_root = Path(project).resolve()
    tried = []
    ordered = []
    if preferred is not None and str(preferred).strip():
        ordered.append(str(preferred).strip())
    for candidate in CHANGES_FILE_CANDIDATES:
        if candidate not in ordered:
            ordered.append(candidate)

    for rel in ordered:
        tried.append(rel)
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            continue
        target = (project_root / p).resolve()
        try:
            target.relative_to(project_root)
        except ValueError:
            continue
        if target.is_file():
            return rel.replace("\\", "/")

    raise PathValidationError(
        "No changes file found in project (tried: {0})".format(
            ", ".join(tried)
        )
    )


def resolve_changes_file(project, changes_file, *, allow_fallback=True):
    """Resolve a changes file strictly inside the project (no free absolutes).

    V0.8.10: when ``changes_file`` is a loose default name (e.g. ``changes.json``)
    and missing, fall back to ``pick_existing_changes_file`` so multi-stage
    mutation stages can run after auto-advance.
    """
    if not changes_file:
        raise PathValidationError("Invalid changes file path.")
    p = Path(changes_file)
    if p.is_absolute():
        # Absolute paths must still resolve inside the project tree.
        target = p.resolve()
        try:
            rel = target.relative_to(Path(project).resolve())
        except ValueError as exc:
            raise PathValidationError(
                "Changes file is outside project."
            ) from exc
        _reject_git_parts(rel.parts)
        if not target.is_file():
            raise PathValidationError(
                f"Changes file does not exist: {changes_file}"
            )
        return target

    try:
        return resolve_file(project, changes_file)
    except PathValidationError:
        if not allow_fallback or not _changes_file_is_loose_default(changes_file):
            raise
        picked = pick_existing_changes_file(project, preferred=None)
        # Skip re-picking the same missing name.
        if picked == str(changes_file).replace("\\", "/"):
            raise
        return resolve_file(project, picked)
