#!/usr/bin/env python3
"""Read-only scan for goal_plans JSON shape corruption (empty dict vs list)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path


DEFAULT_BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DEFAULT_DB = DEFAULT_BASE / "brain" / "anu-brain.db"


def db_path_from_argv():
    if len(sys.argv) > 1 and sys.argv[1] and not sys.argv[1].startswith("-"):
        return Path(sys.argv[1])
    return DEFAULT_DB


def find_corruption(body):
    issues = []
    actions = body.get("actions") if isinstance(body, dict) else None
    if not isinstance(actions, list):
        return [{"field": "actions", "problem": "not_a_list"}]
    for idx, action in enumerate(actions):
        if not isinstance(action, dict):
            issues.append({"action_index": idx, "field": "action", "problem": "not_a_dict"})
            continue
        params = action.get("parameters")
        if not isinstance(params, dict):
            continue
        files = params.get("files")
        if isinstance(files, dict) and len(files) == 0:
            issues.append({
                "action_index": idx,
                "action_id": action.get("action_id"),
                "field": "parameters.files",
                "problem": "empty_dict_should_be_list",
            })
        depends_on = params.get("depends_on")
        if isinstance(depends_on, dict) and len(depends_on) == 0:
            issues.append({
                "action_index": idx,
                "action_id": action.get("action_id"),
                "field": "parameters.depends_on",
                "problem": "empty_dict_should_be_list",
            })
    return issues


def scan(db_file):
    if not Path(db_file).is_file():
        return {
            "ok": False,
            "db": str(db_file),
            "error": "database file not found",
            "scanned": 0,
            "corrupt": [],
        }
    db = sqlite3.connect(str(db_file))
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """SELECT id, goal_id, plan_id, version, status, body_json
               FROM goal_plans ORDER BY goal_id, version"""
        ).fetchall()
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "db": str(db_file),
            "error": str(exc),
            "scanned": 0,
            "corrupt": [],
        }
    finally:
        db.close()

    corrupt = []
    for row in rows:
        try:
            body = json.loads(row["body_json"])
        except json.JSONDecodeError as exc:
            corrupt.append({
                "row_id": row["id"],
                "goal_id": row["goal_id"],
                "plan_id": row["plan_id"],
                "version": row["version"],
                "status": row["status"],
                "issues": [{"field": "body_json", "problem": f"invalid_json: {exc}"}],
            })
            continue
        issues = find_corruption(body)
        if issues:
            corrupt.append({
                "row_id": row["id"],
                "goal_id": row["goal_id"],
                "plan_id": row["plan_id"],
                "version": row["version"],
                "status": row["status"],
                "issues": issues,
            })

    return {
        "ok": True,
        "db": str(db_file),
        "scanned": len(rows),
        "corrupt_count": len(corrupt),
        "corrupt": corrupt,
    }


def main():
    db_file = db_path_from_argv()
    summary = scan(db_file)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
