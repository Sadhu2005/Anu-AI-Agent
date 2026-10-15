#!/usr/bin/env python3
"""ANU Recovery v0.1 (V0.6.0).

Reconciles work interrupted by a crash, kill, timeout, or reboot:

- every mutation transaction still ``STARTED`` is resolved from its
  checkpoint manifest to ``INTERRUPTED_NOT_APPLIED``,
  ``INTERRUPTED_ROLLED_BACK``, or ``INTERRUPTED_CONFLICT``;
- every task left in an in-flight status is resolved from its transaction.

An interrupted change is rolled back, never committed (ADR-013). Recovery
runs only while it holds the execution lock exclusively; a Task Engine or
Safe Mutation run holds it shared, so live work is never mistaken for an
orphan.
"""

import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


BASE = Path("/srv/anu")
DB_FILE = BASE / "brain" / "anu-brain.db"
CHECKPOINT_ROOT = BASE / "checkpoints" / "mutations"
WORKSPACE = BASE / "workspace" / "projects"
LOG_FILE = BASE / "logs" / "anu-recovery.jsonl"
EXECUTION_LOCK_FILE = BASE / "runtime" / "anu-execution.lock"

IN_FLIGHT = ("planning", "ready", "running", "verifying")

NOT_APPLIED = "INTERRUPTED_NOT_APPLIED"
ROLLED_BACK = "INTERRUPTED_ROLLED_BACK"
CONFLICT = "INTERRUPTED_CONFLICT"


class Conflict(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(path.read_bytes())


def open_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        # Root-run tests and the anu-agent service share this file.
        os.fchmod(fd, 0o644)
    except PermissionError:
        pass
    return fd


@contextmanager
def exclusive_execution_lock(lock_path=None):
    fd = open_lock(lock_path or EXECUTION_LOCK_FILE)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def write_log(record):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": now_iso(), **record}, sort_keys=True) + "\n")
    except OSError:
        pass


def add_event(db, task_id, event_type, message, metadata):
    goal_id = parent_task_id = None
    if task_id is not None:
        row = db.execute(
            "SELECT goal_id, parent_task_id FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row:
            goal_id, parent_task_id = row
    db.execute(
        """INSERT INTO agent_events
           (task_id, goal_id, parent_task_id, run_id, attempt,
            event_type, message, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            task_id,
            goal_id,
            parent_task_id,
            metadata.get("run_id"),
            metadata.get("attempt"),
            event_type,
            message,
            json.dumps(metadata, sort_keys=True),
            now_iso(),
        ),
    )


def inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def restore(target, checkpoint, mode, baseline_sha):
    data = checkpoint.read_bytes()
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.anu-recovery-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if sha256_file(target) != baseline_sha:
        raise Conflict(f"restore verification failed: {target}")


def assess_files(project_name, checkpoint_dir, manifest):
    project_dir = WORKSPACE / project_name
    if not inside(project_dir, WORKSPACE) or not project_dir.is_dir():
        raise Conflict(f"project missing or outside workspace: {project_name}")

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise Conflict("manifest lists no files")

    assessed = []
    for item in files:
        rel = item.get("path")
        target = project_dir / str(rel)
        checkpoint = Path(str(item.get("checkpoint")))
        baseline_sha = item.get("baseline_sha256")
        old, new = item.get("old"), item.get("new")

        if not rel or not inside(target, project_dir):
            raise Conflict(f"target outside project: {rel}")
        if not inside(checkpoint, checkpoint_dir) or not checkpoint.is_file():
            raise Conflict(f"checkpoint missing: {rel}")
        if not isinstance(old, str) or not isinstance(new, str) or not baseline_sha:
            raise Conflict(f"manifest entry incomplete: {rel}")

        baseline = checkpoint.read_bytes()
        if sha256_bytes(baseline) != baseline_sha:
            raise Conflict(f"checkpoint integrity mismatch: {rel}")

        text = baseline.decode("utf-8")
        if text.count(old) != 1:
            raise Conflict(f"cannot derive applied content: {rel}")
        applied_sha = sha256_bytes(text.replace(old, new, 1).encode("utf-8"))

        current_sha = sha256_file(target) if target.is_file() else None
        if current_sha == baseline_sha:
            state = "baseline"
        elif current_sha == applied_sha:
            state = "applied"
        else:
            state = "unknown"

        assessed.append({
            "path": rel,
            "target": target,
            "checkpoint": checkpoint,
            "mode": item.get("mode"),
            "state": state,
            "baseline_sha256": baseline_sha,
            "applied_sha256": applied_sha,
            "current_sha256": current_sha,
        })

    unknown = [a["path"] for a in assessed if a["state"] == "unknown"]
    if unknown:
        raise Conflict(f"files match neither baseline nor applied change: {unknown}")
    return assessed


def reconcile_transaction(db, row):
    tx_id = row["transaction_id"]
    project = row["project"]
    checkpoint_dir = CHECKPOINT_ROOT / project / tx_id
    manifest_path = checkpoint_dir / "manifest.json"
    evidence = {"checkpoint_dir": str(checkpoint_dir), "files": []}
    restored = {}

    try:
        if not manifest_path.is_file():
            recorded = db.execute(
                "SELECT 1 FROM checkpoints WHERE phase = ? LIMIT 1",
                (f"mutation:{tx_id}:PRE_MUTATION",),
            ).fetchone()
            if recorded:
                raise Conflict("checkpoint was recorded but its manifest is missing")
            status = NOT_APPLIED
            evidence["reason"] = "interrupted before the checkpoint; no file was changed"
        else:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise Conflict(f"manifest unreadable: {exc}")

            assessed = assess_files(project, checkpoint_dir, manifest)
            for a in assessed:
                if a["state"] == "applied":
                    restore(a["target"], a["checkpoint"], a["mode"] or 0o644, a["baseline_sha256"])
                    restored[a["path"]] = a["baseline_sha256"]
                evidence["files"].append({
                    k: a[k] for k in ("path", "state", "baseline_sha256", "applied_sha256", "current_sha256")
                })
            status = ROLLED_BACK if restored else NOT_APPLIED
            evidence["reason"] = (
                "applied change restored from checkpoint" if restored
                else "files already at baseline"
            )
    except Conflict as exc:
        status = CONFLICT
        evidence["reason"] = str(exc)
        if restored:
            evidence["restored_before_conflict"] = sorted(restored)

    try:
        details = json.loads(row["details"] or "{}")
    except ValueError:
        details = {"previous_details": row["details"]}
    details["recovery"] = evidence

    db.execute(
        """UPDATE mutation_transactions
           SET status = ?, completed_at = ?, checkpoint_path = ?,
               rollback_attempted = ?, rollback_sha256 = ?,
               integrity_verified = ?, details = ?
           WHERE transaction_id = ? AND status = 'STARTED'""",
        (
            status,
            now_iso(),
            str(checkpoint_dir) if checkpoint_dir.is_dir() else None,
            int(bool(restored)),
            sha256_bytes(json.dumps(sorted(restored.items())).encode()) if restored else None,
            int(status != CONFLICT),
            json.dumps(details, sort_keys=True),
            tx_id,
        ),
    )

    result = {
        "transaction_id": tx_id,
        "task_id": row["task_id"],
        "status": status,
        "reason": evidence["reason"],
    }
    add_event(
        db, row["task_id"], "RECOVERY_TRANSACTION",
        f"{tx_id}: STARTED -> {status}",
        {**result, "evidence": evidence},
    )
    write_log({"event": "RECOVERY_TRANSACTION", **result, "evidence": evidence})
    return result


def reconcile_transactions(db):
    rows = db.execute(
        """SELECT transaction_id, project, task_id, details
           FROM mutation_transactions
           WHERE status = 'STARTED' AND transaction_id IS NOT NULL
           ORDER BY id"""
    ).fetchall()
    return [reconcile_transaction(db, row) for row in rows]


def task_outcome(mutation_status):
    if mutation_status in ("COMMITTED", "ALREADY_COMMITTED"):
        return "completed", "change was committed before the process stopped"
    if mutation_status == "ROLLED_BACK":
        return "failed", "test failed and was rolled back before the process stopped"
    if mutation_status in (None, NOT_APPLIED, ROLLED_BACK):
        return "failed", "interrupted"
    return "blocked", f"mutation ended {mutation_status}"


def reconcile_task(db, task):
    task_id = task["id"]
    start = db.execute(
        """SELECT run_id, attempt, created_at FROM agent_events
           WHERE task_id = ? AND event_type = 'TASK_STATE' AND message = 'planning'
           ORDER BY id DESC LIMIT 1""",
        (task_id,),
    ).fetchone()

    tx = None
    if start:
        tx = db.execute(
            """SELECT transaction_id, status FROM mutation_transactions
               WHERE task_id = ? AND created_at >= ?
               ORDER BY id DESC LIMIT 1""",
            (task_id, start["created_at"]),
        ).fetchone()

    mutation_status = tx["status"] if tx else None
    new_status, reason = task_outcome(mutation_status)

    db.execute(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, now_iso(), task_id),
    )

    result = {
        "task_id": task_id,
        "previous_status": task["status"],
        "status": new_status,
        "reason": reason,
        "transaction_id": tx["transaction_id"] if tx else None,
        "mutation_status": mutation_status,
        "run_id": start["run_id"] if start else None,
        "attempt": start["attempt"] if start else task["attempts"],
    }
    add_event(
        db, task_id, "RECOVERY_TASK",
        f"task {task['status']} -> {new_status}: {reason}",
        result,
    )
    write_log({"event": "RECOVERY_TASK", **result})
    return result


def reconcile_tasks(db):
    rows = db.execute(
        f"""SELECT id, status, attempts FROM tasks
            WHERE status IN ({', '.join('?' for _ in IN_FLIGHT)})
            ORDER BY id""",
        IN_FLIGHT,
    ).fetchall()
    return [reconcile_task(db, row) for row in rows]


def recover(db_path=None, lock_path=None):
    """Reconcile interrupted work. Safe to call at any time.

    Returns ``{"status": "DEFERRED_ACTIVE_EXECUTION"}`` without reading or
    changing anything while any Task Engine or Safe Mutation run is active.
    """
    with exclusive_execution_lock(lock_path) as acquired:
        if not acquired:
            return {"status": "DEFERRED_ACTIVE_EXECUTION"}

        with sqlite3.connect(str(db_path or DB_FILE)) as db:
            db.row_factory = sqlite3.Row
            transactions = reconcile_transactions(db)
            tasks = reconcile_tasks(db)
            db.commit()

    return {
        "status": "RECOVERED" if transactions or tasks else "CLEAN",
        "transactions": transactions,
        "tasks": tasks,
    }


if __name__ == "__main__":
    raise SystemExit(
        "anu-recovery-v0.1.py is a library module; the runtime invokes "
        "recover() as anu-agent (a root run would leave root-owned files)"
    )
