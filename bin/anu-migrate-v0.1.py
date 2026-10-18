#!/usr/bin/env python3
"""Apply ordered SQL migrations and record them in schema_migrations.

Idempotent: re-running skips files whose version is already recorded.
Migration version is the filename stem (e.g. 0005_schema_migrations).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DEFAULT_DB = BASE / "brain" / "anu-brain.db"
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
MIGRATIONS_DIR = _REPO / "migrations"


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


def apply_file(db, path: Path) -> bool:
    """Apply one migration; return True if applied, False if already recorded."""
    version = path.stem
    if version in applied_versions(db):
        return False
    sql = path.read_text(encoding="utf-8")
    db.executescript(sql)
    db.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
        (version, now_iso()),
    )
    db.commit()
    return True


def run_migrate(db_path: Path, migrations_dir: Path) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    skipped: list[str] = []
    db = db_connect(db_path)
    try:
        ensure_schema_migrations_table(db)
        done = applied_versions(db)
        for path in migration_files(migrations_dir):
            version = path.stem
            if version in done:
                skipped.append(version)
                continue
            apply_file(db, path)
            applied.append(version)
            done.add(version)
    finally:
        db.close()
    return {
        "ok": True,
        "db": str(db_path),
        "migrations_dir": str(migrations_dir),
        "applied": applied,
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
