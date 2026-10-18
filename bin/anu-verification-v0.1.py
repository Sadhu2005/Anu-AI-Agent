#!/usr/bin/env python3
"""ANU Verification / Evaluation service v0.1 (V0.6.5).

```text
PLAN → EXECUTE → OBSERVE → VERIFY → ACCEPT | RETRY | ROLLBACK
```

Consumes Safe Mutation outcomes (and optional working-tree change lists)
and persists an auditable verification record per task attempt.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
DB_FILE = BASE / "brain" / "anu-brain.db"

DECISION_ACCEPT = "ACCEPT"
DECISION_RETRY = "RETRY"
DECISION_ROLLBACK = "ROLLBACK"
DECISIONS = frozenset({DECISION_ACCEPT, DECISION_RETRY, DECISION_ROLLBACK})

MUTATION_COMMITTED = frozenset({"COMMITTED", "ALREADY_COMMITTED"})
MUTATION_RETRY = frozenset({"ROLLED_BACK", "ABORTED_BASELINE_FAILURE"})
MUTATION_ROLLBACK = frozenset({
    "ROLLBACK_VERIFICATION_FAILED",
    "MUTATION_ENGINE_ERROR",
    "INTERRUPTED_CONFLICT",
})


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _module_path(name):
    for candidate in (
        BASE / "bin" / name,
        _HERE / name,
        _REPO / "bin" / name,
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(name)


def _anu_db():
    path = _module_path("anu-db-v0.1.py")
    spec = importlib.util.spec_from_file_location(
        "anu_db_v0_1_verification",
        path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_observation_memory():
    """Best-effort load of observation/memory (V0.8.0)."""
    try:
        path = _module_path("anu-observation-memory-v0.1.py")
    except FileNotFoundError:
        return None
    spec = importlib.util.spec_from_file_location(
        "anu_observation_memory_v0_1_verification",
        path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve_project_key(
    db,
    *,
    project_key=None,
    mutation_result=None,
    goal_id=None,
    task_id=None,
):
    """Resolve project_key from arg, mutation result, or goal/task join."""
    if isinstance(project_key, str) and project_key.strip():
        return project_key.strip()
    result = mutation_result or {}
    for key in ("project_key", "project"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    try:
        if goal_id is not None:
            row = db.execute(
                """
                SELECT p.project_key
                FROM goals g
                JOIN projects p ON p.id = g.project_id
                WHERE g.id = ?
                """,
                (goal_id,),
            ).fetchone()
            if row is not None:
                key = row[0] if not isinstance(row, sqlite3.Row) else row["project_key"]
                if isinstance(key, str) and key.strip():
                    return key.strip()
        if task_id is not None:
            row = db.execute(
                """
                SELECT p.project_key
                FROM tasks t
                JOIN goals g ON g.id = t.goal_id
                JOIN projects p ON p.id = g.project_id
                WHERE t.id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is not None:
                key = row[0] if not isinstance(row, sqlite3.Row) else row["project_key"]
                if isinstance(key, str) and key.strip():
                    return key.strip()
    except sqlite3.Error:
        return None
    return None


def _record_verification_memory_best_effort(
    db,
    *,
    decision,
    evidence,
    operation=None,
    project_key=None,
    mutation_result=None,
    task_id=None,
    goal_id=None,
):
    """V0.8.0: persist verification decision as observation + memory."""
    mem = _load_observation_memory()
    if mem is None or db is None:
        return None
    resolved = _resolve_project_key(
        db,
        project_key=project_key,
        mutation_result=mutation_result,
        goal_id=goal_id,
        task_id=task_id,
    )
    if not resolved:
        return None
    try:
        mem.migrate(db)
        return mem.record_verification_outcome(
            db,
            project_key=resolved,
            decision=decision,
            operation=operation,
            task_id=task_id,
            goal_id=goal_id,
            evidence=evidence,
        )
    except Exception:
        return None


def db_connect(db_path=None):
    return _anu_db().db_connect(db_path if db_path is not None else DB_FILE)


def _require_db(db):
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")
    return db


def migrate(db):
    """Apply V0.6.5 verification_records DDL (idempotent)."""
    db = _require_db(db)
    sql_path = _REPO / "migrations" / "0008_v0_6_5.sql"
    if not sql_path.is_file():
        sql_path = BASE / "migrations" / "0008_v0_6_5.sql"
    sql = sql_path.read_text(encoding="utf-8")
    for stmt in _split_sql(sql):
        try:
            db.execute(stmt)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "duplicate" in msg:
                continue
            raise
    db.commit()


def _split_sql(sql: str):
    parts = []
    for chunk in sql.split(";"):
        lines = [
            line
            for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        text = "\n".join(lines).strip()
        if text:
            parts.append(text)
    return parts


def _norm_path(path):
    text = str(path or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def parse_git_status_short(stdout):
    """Parse ``git status --short`` into relative path strings."""
    paths = []
    if not stdout:
        return paths
    for line in str(stdout).splitlines():
        if not line.strip():
            continue
        # XY PATH or XY ORIG -> PATH (rename)
        body = line[3:] if len(line) >= 3 and line[2] == " " else line.strip()
        if " -> " in body:
            body = body.split(" -> ", 1)[1]
        body = body.strip().strip('"')
        if body:
            paths.append(_norm_path(body))
    return paths


def check_diff_scope(declared_files, actual_changed_files):
    """Return (ok, extras) — extras are paths outside the declared set."""
    declared = {_norm_path(p) for p in (declared_files or []) if p}
    actual = {_norm_path(p) for p in (actual_changed_files or []) if p}
    if not actual:
        # No external listing: Safe Mutation only writes declared files.
        return True, []
    extras = sorted(p for p in actual if p not in declared)
    return len(extras) == 0, extras


def check_intended_changes(file_digests):
    """Return (ok, unchanged_paths).

    ``file_digests`` is ``[{path, baseline_sha256, after_sha256}, ...]``.
    Intended means every declared file content differs from baseline.
    """
    unchanged = []
    if not file_digests:
        return False, ["(no file digests)"]
    for item in file_digests:
        path = _norm_path(item.get("path"))
        baseline = item.get("baseline_sha256")
        after = item.get("after_sha256")
        if not path or not baseline or not after:
            unchanged.append(path or "(missing)")
            continue
        if baseline == after:
            unchanged.append(path)
    return len(unchanged) == 0, unchanged


def check_checkpoint_integrity(file_digests):
    """Checkpoint OK when baseline SHAs are present for every declared file."""
    if not file_digests:
        return False
    for item in file_digests:
        if not item.get("baseline_sha256"):
            return False
        if not item.get("path"):
            return False
    return True


def decide_from_checks(
    *,
    mutation_status,
    compile_ok,
    tests_ok,
    intended_ok,
    unintended_ok,
    checkpoint_ok,
    rollback_ok,
):
    """Map evidence flags + mutation status → ACCEPT | RETRY | ROLLBACK."""
    status = str(mutation_status or "")

    if unintended_ok is False:
        return DECISION_ROLLBACK

    if status in MUTATION_ROLLBACK:
        return DECISION_ROLLBACK

    if checkpoint_ok is False and status in MUTATION_COMMITTED:
        return DECISION_ROLLBACK

    if status == "ROLLBACK_VERIFICATION_FAILED":
        return DECISION_ROLLBACK

    if rollback_ok is False and status == "ROLLED_BACK":
        return DECISION_ROLLBACK

    if status in MUTATION_RETRY:
        return DECISION_RETRY

    if status in MUTATION_COMMITTED:
        if tests_ok is False or intended_ok is False:
            return DECISION_ROLLBACK
        if compile_ok is False:
            return DECISION_RETRY
        if checkpoint_ok is False:
            return DECISION_ROLLBACK
        return DECISION_ACCEPT

    # Unknown / error statuses fail closed.
    return DECISION_ROLLBACK


def _bool_int(value):
    if value is None:
        return None
    return 1 if bool(value) else 0


def _row_to_record(row):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    evidence = row.get("evidence_json")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = {"raw": evidence}
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "goal_id": row.get("goal_id"),
        "attempt": row["attempt"],
        "run_id": row.get("run_id"),
        "transaction_id": row.get("transaction_id"),
        "decision": row["decision"],
        "compile_ok": None if row.get("compile_ok") is None else bool(row["compile_ok"]),
        "tests_ok": None if row.get("tests_ok") is None else bool(row["tests_ok"]),
        "intended_ok": None if row.get("intended_ok") is None else bool(row["intended_ok"]),
        "unintended_ok": (
            None if row.get("unintended_ok") is None else bool(row["unintended_ok"])
        ),
        "checkpoint_ok": (
            None if row.get("checkpoint_ok") is None else bool(row["checkpoint_ok"])
        ),
        "rollback_ok": (
            None if row.get("rollback_ok") is None else bool(row["rollback_ok"])
        ),
        "goal_ok": None if row.get("goal_ok") is None else bool(row["goal_ok"]),
        "evidence": evidence,
        "created_at": row["created_at"],
    }


def persist_record(
    db,
    *,
    task_id,
    attempt,
    decision,
    evidence,
    goal_id=None,
    run_id=None,
    transaction_id=None,
    compile_ok=None,
    tests_ok=None,
    intended_ok=None,
    unintended_ok=None,
    checkpoint_ok=None,
    rollback_ok=None,
    goal_ok=None,
):
    db = _require_db(db)
    if decision not in DECISIONS:
        raise ValueError(f"invalid decision: {decision}")
    if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id < 1:
        raise ValueError("task_id must be a positive integer")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise ValueError("attempt must be a positive integer")

    created = now_iso()
    evidence_json = json.dumps(evidence if evidence is not None else {}, sort_keys=True)
    cur = db.execute(
        """
        INSERT INTO verification_records (
            task_id, goal_id, attempt, run_id, transaction_id, decision,
            compile_ok, tests_ok, intended_ok, unintended_ok,
            checkpoint_ok, rollback_ok, goal_ok,
            evidence_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            goal_id,
            attempt,
            run_id,
            transaction_id,
            decision,
            _bool_int(compile_ok),
            _bool_int(tests_ok),
            _bool_int(intended_ok),
            _bool_int(unintended_ok),
            _bool_int(checkpoint_ok),
            _bool_int(rollback_ok),
            _bool_int(goal_ok),
            evidence_json,
            created,
        ),
    )
    db.commit()
    record_id = cur.lastrowid
    return get_record(db, record_id)


def get_record(db, record_id):
    db = _require_db(db)
    row = db.execute(
        "SELECT * FROM verification_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    return _row_to_record(row)


def get_latest_for_task(db, task_id):
    """Latest *task-level* record (excludes goal-acceptance rows)."""
    db = _require_db(db)
    row = db.execute(
        """
        SELECT * FROM verification_records
        WHERE task_id = ? AND goal_ok IS NULL
        ORDER BY attempt DESC, id DESC
        LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    return _row_to_record(row)


def require_accept_for_complete(db, task_id, attempt=None):
    """Return latest ACCEPT record or raise ``VerificationError``."""
    db = _require_db(db)
    if attempt is None:
        record = get_latest_for_task(db, task_id)
    else:
        row = db.execute(
            """
            SELECT * FROM verification_records
            WHERE task_id = ? AND attempt = ? AND goal_ok IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id, attempt),
        ).fetchone()
        record = _row_to_record(row)

    if record is None:
        raise VerificationError(
            f"task {task_id}: no verification record; cannot COMPLETE"
        )
    if record["decision"] != DECISION_ACCEPT:
        raise VerificationError(
            f"task {task_id}: verification {record['decision']}; cannot COMPLETE"
        )
    return record


class VerificationError(RuntimeError):
    pass


def _file_digests_from_mutation(mutation_result, declared_files):
    details = (mutation_result or {}).get("details") or {}
    post = details.get("post_mutation_sha256") or {}
    files = (mutation_result or {}).get("file_records") or []
    digests = []
    if files:
        for item in files:
            path = _norm_path(item.get("path"))
            digests.append({
                "path": path,
                "baseline_sha256": item.get("baseline_sha256"),
                "after_sha256": item.get("after_sha256") or post.get(path),
            })
        return digests

    baseline_map = details.get("baseline_sha256_by_file") or {}
    for path in declared_files or (mutation_result or {}).get("files") or []:
        path = _norm_path(path)
        digests.append({
            "path": path,
            "baseline_sha256": baseline_map.get(path),
            "after_sha256": post.get(path),
        })
    return digests


def evaluate_mutation_attempt(
    db,
    mutation_result,
    *,
    task_id,
    attempt,
    run_id=None,
    goal_id=None,
    declared_files=None,
    actual_changed_files=None,
    operation=None,
    persist=True,
    project_key=None,
):
    """Build evidence, decide ACCEPT|RETRY|ROLLBACK, optionally persist."""
    db = _require_db(db)
    result = mutation_result or {}
    status = result.get("status")
    declared = [
        _norm_path(p)
        for p in (
            declared_files
            if declared_files is not None
            else result.get("files") or []
        )
    ]

    if actual_changed_files is None:
        actual_changed_files = result.get("actual_changed_files")
    if actual_changed_files is None:
        short = (result.get("details") or {}).get("git_status_short")
        if isinstance(short, dict):
            stdout = (
                (short.get("result") or {}).get("stdout")
                if isinstance(short.get("result"), dict)
                else short.get("stdout")
            )
            if stdout is None and isinstance(short.get("payload"), dict):
                stdout = short["payload"].get("stdout")
            actual_changed_files = parse_git_status_short(stdout or "")
        elif isinstance(short, str):
            actual_changed_files = parse_git_status_short(short)

    unintended_ok, extras = check_diff_scope(declared, actual_changed_files)

    digests = _file_digests_from_mutation(result, declared)
    # On ALREADY_COMMITTED / COMMITTED without digests, treat intended as
    # satisfied only when mutation reported success (Safe Mutation applied).
    if digests and any(d.get("after_sha256") for d in digests):
        intended_ok, unchanged = check_intended_changes(digests)
        checkpoint_ok = check_checkpoint_integrity(digests)
    elif status in MUTATION_COMMITTED:
        intended_ok = True
        unchanged = []
        checkpoint_ok = bool(result.get("integrity_verified", True))
    elif status == "ROLLED_BACK":
        intended_ok = None
        unchanged = []
        checkpoint_ok = bool(result.get("integrity_verified"))
    else:
        intended_ok = None
        unchanged = []
        checkpoint_ok = result.get("integrity_verified")

    tests_ok = result.get("mutation_test_ok")
    if status == "ABORTED_BASELINE_FAILURE":
        tests_ok = False
    if status == "ROLLED_BACK":
        tests_ok = False

    op = operation or result.get("operation") or (
        (result.get("details") or {}).get("operation")
    )
    if op == "python_compile":
        compile_ok = bool(tests_ok) if tests_ok is not None else None
    elif op == "python_test":
        # Tests imply compile for the exercised modules.
        compile_ok = True if tests_ok else (False if tests_ok is False else None)
    else:
        compile_ok = None

    rollback_ok = None
    if result.get("rollback_attempted"):
        rollback_ok = bool(
            result.get("rollback_verified") and result.get("integrity_verified")
        )

    decision = decide_from_checks(
        mutation_status=status,
        compile_ok=compile_ok,
        tests_ok=tests_ok,
        intended_ok=intended_ok,
        unintended_ok=unintended_ok,
        checkpoint_ok=checkpoint_ok,
        rollback_ok=rollback_ok,
    )

    evidence = {
        "mutation_status": status,
        "operation": op,
        "declared_files": declared,
        "actual_changed_files": list(actual_changed_files or []),
        "unintended_extras": extras,
        "unchanged_intended": unchanged,
        "file_digests": digests,
        "baseline_ok": result.get("baseline_ok"),
        "mutation_test_ok": result.get("mutation_test_ok"),
        "integrity_verified": result.get("integrity_verified"),
        "rollback_attempted": result.get("rollback_attempted"),
        "rollback_verified": result.get("rollback_verified"),
        "questions": {
            "compile": compile_ok,
            "tests": tests_ok,
            "intended_change": intended_ok,
            "no_unintended": unintended_ok,
            "checkpoint": checkpoint_ok,
            "rollback": rollback_ok,
        },
    }

    record = {
        "task_id": task_id,
        "goal_id": goal_id,
        "attempt": attempt,
        "run_id": run_id,
        "transaction_id": result.get("transaction_id"),
        "decision": decision,
        "compile_ok": compile_ok,
        "tests_ok": tests_ok,
        "intended_ok": intended_ok,
        "unintended_ok": unintended_ok,
        "checkpoint_ok": checkpoint_ok,
        "rollback_ok": rollback_ok,
        "goal_ok": None,
        "evidence": evidence,
    }

    if persist:
        record = persist_record(
            db,
            task_id=task_id,
            goal_id=goal_id,
            attempt=attempt,
            run_id=run_id,
            transaction_id=result.get("transaction_id"),
            decision=decision,
            compile_ok=compile_ok,
            tests_ok=tests_ok,
            intended_ok=intended_ok,
            unintended_ok=unintended_ok,
            checkpoint_ok=checkpoint_ok,
            rollback_ok=rollback_ok,
            goal_ok=None,
            evidence=evidence,
        )
        # V0.8.0: best-effort self-observation; never fail verification.
        mem_result = _record_verification_memory_best_effort(
            db,
            decision=decision,
            evidence=evidence,
            operation=op,
            project_key=project_key,
            mutation_result=result,
            task_id=task_id,
            goal_id=goal_id,
        )
        if mem_result and isinstance(record, dict):
            record["memory_id"] = (mem_result.get("memory") or {}).get("id")
            record["observation_id"] = mem_result.get("observation_id")
    return record


def evaluate_goal_acceptance(db, goal_id, *, persist=True):
    """Goal is accepted only when every task has a latest ACCEPT record."""
    db = _require_db(db)
    if not isinstance(goal_id, int) or isinstance(goal_id, bool) or goal_id < 1:
        raise ValueError("goal_id must be a positive integer")

    tasks = db.execute(
        """
        SELECT id, status FROM tasks
        WHERE goal_id = ?
        ORDER BY id ASC
        """,
        (goal_id,),
    ).fetchall()

    ignored = frozenset({"superseded", "cancelled"})
    task_results = []
    all_ok = True
    for row in tasks:
        if isinstance(row, sqlite3.Row):
            row = dict(row)
        status = row["status"]
        if status in ignored:
            continue
        latest = get_latest_for_task(db, row["id"])
        ok = (
            status == "completed"
            and latest is not None
            and latest["decision"] == DECISION_ACCEPT
        )
        if not ok:
            all_ok = False
        task_results.append({
            "task_id": row["id"],
            "status": status,
            "verification_id": None if latest is None else latest["id"],
            "decision": None if latest is None else latest["decision"],
            "ok": ok,
        })

    if not task_results:
        all_ok = False

    evidence = {
        "goal_id": goal_id,
        "tasks": task_results,
        "questions": {"goal_satisfied": all_ok},
    }

    decision = DECISION_ACCEPT if all_ok else DECISION_RETRY
    # Persist against the first task when available (schema requires task_id).
    anchor_task = task_results[0]["task_id"] if task_results else None
    record = {
        "goal_id": goal_id,
        "decision": decision,
        "goal_ok": all_ok,
        "evidence": evidence,
        "task_id": anchor_task,
    }
    if persist and anchor_task is not None:
        record = persist_record(
            db,
            task_id=anchor_task,
            goal_id=goal_id,
            attempt=1,
            decision=decision,
            goal_ok=all_ok,
            evidence=evidence,
        )
    return record


def decision_to_task_outcome(decision, mutation_status=None):
    """Map verification decision onto task-engine status + failure_class."""
    if decision == DECISION_ACCEPT:
        return {
            "task_status": "completed",
            "failure_class": None,
            "retryable": False,
        }
    if decision == DECISION_RETRY:
        return {
            "task_status": "failed",
            "failure_class": "code_defect",
            "retryable": True,
        }
    # ROLLBACK
    failure = "integrity"
    if mutation_status == "ABORTED_BASELINE_FAILURE":
        failure = "environment"
    elif mutation_status == "MUTATION_ENGINE_ERROR":
        failure = "contract"
    return {
        "task_status": "blocked",
        "failure_class": failure,
        "retryable": False,
    }


def main(argv=None):
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--db", default=str(DB_FILE))

    parser = argparse.ArgumentParser(description="ANU Verification v0.1")
    parser.add_argument("--db", default=str(DB_FILE))
    sub = parser.add_subparsers(dest="cmd", required=True)

    # --db accepted before or after the subcommand.
    sub.add_parser("migrate", parents=[parent])

    latest = sub.add_parser("latest", parents=[parent])
    latest.add_argument("--task-id", type=int, required=True)

    goal_p = sub.add_parser("goal-accept", parents=[parent])
    goal_p.add_argument("--goal-id", type=int, required=True)

    eval_p = sub.add_parser("evaluate", parents=[parent])
    eval_p.add_argument("--task-id", type=int, required=True)
    eval_p.add_argument("--attempt", type=int, required=True)
    eval_p.add_argument("--mutation-json", required=True)
    eval_p.add_argument("--goal-id", type=int, default=None)
    eval_p.add_argument("--run-id", default=None)

    args = parser.parse_args(argv)
    db = db_connect(args.db)
    try:
        if args.cmd == "migrate":
            migrate(db)
            print(json.dumps({"ok": True, "migrated": "0008_v0_6_5"}, indent=2))
            return 0
        if args.cmd == "latest":
            migrate(db)
            print(json.dumps({
                "ok": True,
                "record": get_latest_for_task(db, args.task_id),
            }, indent=2))
            return 0
        if args.cmd == "goal-accept":
            migrate(db)
            print(json.dumps({
                "ok": True,
                "record": evaluate_goal_acceptance(db, args.goal_id),
            }, indent=2))
            return 0
        if args.cmd == "evaluate":
            migrate(db)
            mutation = json.loads(Path(args.mutation_json).read_text(encoding="utf-8"))
            record = evaluate_mutation_attempt(
                db,
                mutation,
                task_id=args.task_id,
                attempt=args.attempt,
                goal_id=args.goal_id,
                run_id=args.run_id,
            )
            print(json.dumps({"ok": True, "record": record}, indent=2))
            return 0
        parser.error(f"unknown command: {args.cmd}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
