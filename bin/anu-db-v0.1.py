#!/usr/bin/env python3
"""Shared SQLite connection helper (review F7 / F8).

Every Brain writer should use ``db_connect`` so foreign keys, busy timeout,
and WAL expectations are consistent. Dropped audit events go to stderr with
a process-local counter (F8).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


DEFAULT_BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
DEFAULT_DB = DEFAULT_BASE / "brain" / "anu-brain.db"
BUSY_TIMEOUT_MS = 5000

# Process-local counter of agent_events insert failures (F8).
DROPPED_EVENTS = 0


def db_connect(db_path=None, *, row_factory=sqlite3.Row, check_wal=False):
    """Open a Brain connection with foreign keys enforced."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB
    db = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000.0)
    if row_factory is not None:
        db.row_factory = row_factory
    db.execute("PRAGMA foreign_keys = ON")
    db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if check_wal:
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "wal":
            db.execute("PRAGMA journal_mode = WAL")
    return db


def foreign_keys_enabled(db):
    row = db.execute("PRAGMA foreign_keys").fetchone()
    return int(row[0]) == 1


def note_dropped_event(exc, *, where="agent_events"):
    """Record a failed audit insert on stderr and increment DROPPED_EVENTS."""
    global DROPPED_EVENTS
    DROPPED_EVENTS += 1
    print(
        f"ANU_DROPPED_EVENT count={DROPPED_EVENTS} where={where}: {exc}",
        file=sys.stderr,
        flush=True,
    )
    return DROPPED_EVENTS


def dropped_events_count():
    return DROPPED_EVENTS
