#!/usr/bin/env python3
"""Apply ordered SQL migrations and record them in schema_migrations.

Idempotent for both fresh and already-populated Brains:
- versions already in ``schema_migrations`` are skipped
- statements that raise "already exists" / "duplicate column name" are
  treated as satisfied (live DBs predate the runner)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DEFAULT_DB = BASE / "brain" / "anu-brain.db"
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
MIGRATIONS_DIR = _REPO / "migrations"

# If these objects exist, the corresponding baseline migration is already live.
BASELINE_PRESENT = {
    "0001_initial": "SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'",
    "0002_v0_6_0": (
        "SELECT 1 FROM pragma_table_info('tasks') WHERE name='failure_class'"
    ),
    "0003_v0_6_1": "SELECT 1 FROM sqlite_master WHERE type='table' AND name='goal_plans'",
}

_IGNORABLE = (
    "already exists",
    "duplicate column name",
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db_connect(db_path: Path):
    db = sqlite3.connect(str(db_path), timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")
    return db


def ensure_schema_migrations_table(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        """
    )
    db.commit()


def applied_versions(db) -> set[str]:
    ensure_schema_migrations_table(db)
    rows = db.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def migration_files(migrations_dir: Path) -> list[Path]:
    if not migrations_dir.is_dir():
        return []
    return sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name)


def _split_statements(sql: str) -> list[str]:
    """Split SQL on semicolons; keep non-empty, non-comment-only chunks."""
    parts = []
    for chunk in sql.split(";"):
        lines = []
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            lines.append(line)
        text = "\n".join(lines).strip()
        if text:
            parts.append(text)
    return parts


def _baseline_already_live(db, version: str) -> bool:
    probe = BASELINE_PRESENT.get(version)
    if not probe:
        return False
    try:
        return db.execute(probe).fetchone() is not None
    except sqlite3.Error:
        return False


def _record_applied(db, version: str):
    db.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
        (version, now_iso()),
    )


def apply_file(db, path: Path) -> str:
    """Apply one migration.

    Returns ``applied``, ``recorded`` (baseline already live), or ``skipped``.
    """
    version = path.stem
    if version in applied_versions(db):
        return "skipped"

    if _baseline_already_live(db, version):
        _record_applied(db, version)
        db.commit()
        return "recorded"

    sql = path.read_text(encoding="utf-8")
    # Strip block-style noise; keep statement split tolerant of comments.
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    for stmt in _split_statements(sql):
        try:
            db.execute(stmt)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if any(token in msg for token in _IGNORABLE):
                continue
            raise
    _record_applied(db, version)
    db.commit()
    return "applied"


def run_migrate(db_path: Path, migrations_dir: Path) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    recorded: list[str] = []
    skipped: list[str] = []
    db = db_connect(db_path)
    try:
        ensure_schema_migrations_table(db)
        for path in migration_files(migrations_dir):
            outcome = apply_file(db, path)
            version = path.stem
            if outcome == "applied":
                applied.append(version)
            elif outcome == "recorded":
                recorded.append(version)
            else:
                skipped.append(version)
    finally:
        db.close()
    return {
        "ok": True,
        "db": str(db_path),
        "migrations_dir": str(migrations_dir),
        "applied": applied,
        "recorded_baseline": recorded,
        "skipped": skipped,
    }


def main():
    parser = argparse.ArgumentParser(description="Apply ANU Brain SQL migrations")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="SQLite database path (default ANU_BASE/brain/anu-brain.db)",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=MIGRATIONS_DIR,
        help="Directory containing numbered *.sql files",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print pending/applied migration versions as JSON and exit",
    )
    args = parser.parse_args()

    if args.list:
        files = migration_files(args.migrations_dir)
        if not args.db.is_file():
            payload = {
                "ok": True,
                "pending": [p.stem for p in files],
                "applied": [],
            }
            print(json.dumps(payload, indent=2))
            return 0
        db = db_connect(args.db)
        try:
            done = sorted(applied_versions(db))
        finally:
            db.close()
        all_versions = [p.stem for p in files]
        pending = [v for v in all_versions if v not in done]
        print(
            json.dumps(
                {"ok": True, "applied": done, "pending": pending},
                indent=2,
            )
        )
        return 0

    result = run_migrate(args.db, args.migrations_dir)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
