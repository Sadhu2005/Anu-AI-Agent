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


def resolve_changes_file(project, changes_file):
    """Resolve a changes file strictly inside the project (no free absolutes)."""
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
    return resolve_file(project, changes_file)
