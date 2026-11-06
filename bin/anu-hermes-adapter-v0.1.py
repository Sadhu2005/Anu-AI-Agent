#!/usr/bin/env python3
"""ANU Hermes adapter v0.1 — build a job request. Does not start Hermes."""

from __future__ import annotations

import argparse
import json


ALLOWED_OPERATIONS = (
    "read_files",
    "write_files",
    "run_tests",
    "inspect_logs",
)


def build_job_request(
    *,
    job_id: str,
    project: str,
    base_commit: str,
    goal: str,
    acceptance_criteria: list | None = None,
    allowed_operations: list | None = None,
    role: str = "implement",
    git_push: bool = False,
    production_access: bool = False,
    network_access: bool = False,
) -> dict:
    """Return the V1.4 job request. Push, production, and network stay off."""
    if git_push or production_access or network_access:
        raise ValueError(
            "job refuses git_push, production_access, or network_access"
        )
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id is required")
    if not isinstance(project, str) or not project.strip():
        raise ValueError("project is required")
    if not isinstance(base_commit, str) or not base_commit.strip():
        raise ValueError("base_commit is required")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("goal is required")
    criteria = list(acceptance_criteria or [])
    if any(not isinstance(item, str) or not item.strip() for item in criteria):
        raise ValueError("acceptance_criteria must be strings")
    operations = list(allowed_operations or ALLOWED_OPERATIONS)
    unknown = [item for item in operations if item not in ALLOWED_OPERATIONS]
    if unknown:
        raise ValueError(f"allowed_operations rejected: {unknown[0]}")
    return {
        "job_id": job_id.strip(),
        "project": project.strip(),
        "base_commit": base_commit.strip(),
        "role": (role or "implement").strip() or "implement",
        "goal": goal.strip(),
        "acceptance_criteria": criteria,
        "allowed_operations": operations,
        "network_access": False,
        "git_push": False,
        "production_access": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ANU Hermes adapter v0.1")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--role", default="implement")
    args = parser.parse_args(argv)
    request = build_job_request(
        job_id=args.job_id,
        project=args.project,
        base_commit=args.base_commit,
        goal=args.goal,
        role=args.role,
    )
    print(json.dumps(request, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
