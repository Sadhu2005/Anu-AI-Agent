#!/usr/bin/env python3
"""F7: Brain db_connect helpers must enforce PRAGMA foreign_keys=ON."""

import importlib.util
import sqlite3
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def temp_db_path():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name)


def assert_fk_on(db_mod, path, label):
    db = db_mod.db_connect(path)
    try:
        if hasattr(db_mod, "foreign_keys_enabled"):
            assert db_mod.foreign_keys_enabled(db), f"{label}: foreign_keys off"
        else:
            row = db.execute("PRAGMA foreign_keys").fetchone()
            assert int(row[0]) == 1, f"{label}: foreign_keys off"
    finally:
        db.close()


def test_shared_db_helper():
    path = temp_db_path()
    try:
        db_mod = load(REPO / "bin" / "anu-db-v0.1.py", "anu_db_f7")
        assert_fk_on(db_mod, path, "anu-db-v0.1")
    finally:
        path.unlink(missing_ok=True)
    print("PASS anu-db-v0.1 db_connect enables foreign keys")


def test_service_db_connect_helpers():
    modules = [
        (REPO / "bin" / "anu-goal-service-v0.1.py", "goal_svc_f7"),
        (REPO / "bin" / "anu-goal-plan-service-v0.1.py", "plan_svc_f7"),
        (REPO / "bin" / "anu-goal-aggregator-v0.1.py", "agg_f7"),
        (REPO / "bin" / "anu-goal-recovery-v0.1.py", "recovery_f7"),
    ]
    for module_path, name in modules:
        path = temp_db_path()
        try:
            mod = load(module_path, name)
            assert_fk_on(mod, path, module_path.name)
        finally:
            path.unlink(missing_ok=True)
    print("PASS goal service modules enable foreign keys on connect")


if __name__ == "__main__":
    print("===== ANU F7 DB FOREIGN KEYS REGRESSION =====")
    test_shared_db_helper()
    test_service_db_connect_helpers()
    print("DB_FOREIGN_KEYS_TEST_COMPLETE")
