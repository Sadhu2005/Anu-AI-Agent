#!/usr/bin/env python3
"""Detect corrupt goal_plans bodies; optionally supersede active corrupt rows."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DEFAULT_DB = DEFAULT_BASE / "brain" / "anu-brain.db"

_SCAN = Path(__file__).resolve().parent / "scan_goal_plans_corruption.py"
import importlib.util

_spec = importlib.util.spec_from_file_location("scan_goal_plans_corruption", _SCAN)
_scan_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scan_mod)
find_corruption = _scan_mod.find_corruption


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_corrupt_rows(db_file):
    db = sqlite3.connect(str(db_file))
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """SELECT id, goal_id, plan_id, version, status, body_json
               FROM goal_plans ORDER BY goal_id, version"""
        ).fetchall()
    finally:
        db.close()

    corrupt = []
    for row in rows:
        try:
            body = json.loads(row["body_json"])
        except json.JSONDecodeError as exc:
            issues = [{"field": "body_json", "problem": f"invalid_json: {exc}"}]
        else:
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
    return corrupt


def apply_repairs(db_file, corrupt_rows):
    db = sqlite3.connect(str(db_file))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    applied = []
    try:
        for item in corrupt_rows:
            if item["status"] != "active":
                continue
            db.execute(
                "UPDATE goal_plans SET status = 'superseded' WHERE id = ?",
                (item["row_id"],),
            )
            applied.append({
                **item,
                "action": "superseded_active_corrupt_plan",
                "at": now_iso(),
            })
        db.commit()
    finally:
        db.close()
    return applied


def main(argv=None):
    parser = argparse.ArgumentParser(description="Repair corrupt goal_plans bodies")
    parser.add_argument(
        "db",
        nargs="?",
        default=str(DEFAULT_DB),
        help="SQLite database path (default ANU_BASE/brain/anu-brain.db)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Supersede corrupt active plans (default dry-run)",
    )
    parser.add_argument(
        "--evidence-out",
        default=None,
        help="Optional path to write evidence JSON (stdout always printed)",
    )
    args = parser.parse_args(argv)

    db_file = Path(args.db)
    corrupt = load_corrupt_rows(db_file)
    evidence = {
        "ok": True,
        "db": str(db_file),
        "dry_run": not args.apply,
        "corrupt_count": len(corrupt),
        "corrupt": corrupt,
        "applied": [],
    }

    if args.apply:
        evidence["applied"] = apply_repairs(db_file, corrupt)

    text = json.dumps(evidence, indent=2, sort_keys=True)
    print(text)
    if args.evidence_out:
        Path(args.evidence_out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
