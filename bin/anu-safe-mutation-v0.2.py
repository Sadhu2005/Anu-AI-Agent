#!/usr/bin/env python3

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows host tests; Pi/Linux keep real flock
    fcntl = None

BASE = Path(os.environ.get('ANU_BASE', '/srv/anu'))
GATEWAY = BASE / 'bin' / 'anu-command-gateway.py'
CAPABILITY_ADAPTER = BASE / 'bin' / 'anu-capability-adapter-v0.1.py'
DB_FILE = BASE / 'brain' / 'anu-brain.db'
LOG_FILE = BASE / 'logs' / 'anu-mutations.jsonl'
CHECKPOINT_ROOT = BASE / 'checkpoints' / 'mutations'
WORKSPACE = BASE / 'workspace' / 'projects'
EXECUTION_LOCK_FILE = BASE / 'runtime' / 'anu-execution.lock'
PATHS_MODULE = BASE / 'bin' / 'anu-project-paths-v0.1.py'
DB_MODULE = BASE / 'bin' / 'anu-db-v0.1.py'


def _load_sibling(path, name):
    import importlib.util
    # Prefer checkout sibling when ANU_BASE points elsewhere or live missing.
    candidates = [path, Path(__file__).resolve().parent / path.name]
    for candidate in candidates:
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(name, candidate)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError(f'Unable to load {path}')


def _paths():
    return _load_sibling(PATHS_MODULE, 'anu_project_paths_v0_1_sm')


def _db_helper():
    return _load_sibling(DB_MODULE, 'anu_db_v0_1_sm')


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def utc_stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


@contextlib.contextmanager
def shared_execution_lock():
    """Held for the whole transaction so recovery never treats it as orphaned."""
    EXECUTION_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(EXECUTION_LOCK_FILE, os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        try:
            # Root-run tests and the anu-agent service share this file.
            os.fchmod(fd, 0o644)
        except PermissionError:
            pass
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)


def fail(message, code=1):
    print(json.dumps({'ok': False, 'error': message}, indent=2))
    raise SystemExit(code)


def _load_project_intelligence():
    try:
        return _load_sibling(
            BASE / 'bin' / 'anu-project-intelligence-v0.1.py',
            'anu_project_intelligence_v0_1_sm',
        )
    except Exception:
        return _load_sibling(
            Path(__file__).resolve().parent / 'anu-project-intelligence-v0.1.py',
            'anu_project_intelligence_v0_1_sm',
        )


def _require_project_registered(project_name):
    """V0.6.4: refuse mutation on unregistered projects when rule enabled."""
    try:
        pi = _load_project_intelligence()
    except Exception:
        return
    try:
        pi.require_registered(project_name)
    except Exception as exc:
        # ProjectRegistrationError and path errors.
        fail(str(exc))


def _lookup_goal_id(task_id):
    if task_id is None:
        return None
    try:
        with _db_helper().db_connect(DB_FILE) as db:
            row = db.execute(
                'SELECT goal_id FROM tasks WHERE id = ?',
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            return row[0] if not hasattr(row, 'keys') else row['goal_id']
    except Exception:
        return None


def _gate_production_deployment(task_id=None, proposed_change=None):
    """V0.7.0: pause production-impacting mutations until approved."""
    try:
        pi = _load_project_intelligence()
    except Exception as exc:
        fail(f'production gate unavailable: {exc}')
    goal_id = _lookup_goal_id(task_id)
    try:
        with _db_helper().db_connect(DB_FILE) as db:
            result = pi.gate_production_deployment(
                db,
                task_id=task_id,
                goal_id=goal_id,
                proposed_change=proposed_change,
            )
    except Exception as exc:
        # ApprovalDeniedError and unexpected failures abort closed.
        fail(f'production deployment denied: {exc}')
    if not isinstance(result, dict):
        return result
    if str(result.get('classification', '')).upper() == 'APPROVAL':
        approval = result.get('approval') or {}
        fail(
            'production deployment requires approval '
            f"(approval_id={approval.get('id')})"
        )
    return result


def resolve_project(project_name):
    _require_project_registered(project_name)
    paths = _paths()
    try:
        return paths.resolve_project(WORKSPACE, project_name)
    except paths.PathValidationError as exc:
        fail(str(exc))


def resolve_file(project, relative_file):
    paths = _paths()
    try:
        return paths.resolve_file(project, relative_file)
    except paths.PathValidationError as exc:
        fail(str(exc))


def resolve_changes_file(project, changes_file):
    # F3: absolute paths must still resolve inside the project (no free abs).
    paths = _paths()
    try:
        return paths.resolve_changes_file(project, changes_file)
    except paths.PathValidationError as exc:
        fail(str(exc))


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_text(path):
    return path.read_text(encoding='utf-8')


def atomic_write(path, content):
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', dir=str(path.parent),
        prefix=f'.{path.name}.anu-', delete=False
    ) as tf:
        tmp = Path(tf.name)
        tf.write(content)
        tf.flush()
        os.fsync(tf.fileno())
    try:
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _capability_adapter():
    return _load_sibling(CAPABILITY_ADAPTER, 'anu_capability_adapter_v0_1_sm')


def run_gateway(project_name, operation, task_id=None):
    """Run a named operation through the capability registry (V0.6.2 adapter).

    Falls back to direct gateway only if the adapter module is missing.
    """
    try:
        adapter = _capability_adapter()
    except Exception:
        adapter = None

    if adapter is not None:
        goal_id = None
        if task_id is not None:
            try:
                with _db_helper().db_connect(DB_FILE) as db:
                    row = db.execute(
                        'SELECT goal_id FROM tasks WHERE id = ?',
                        (task_id,),
                    ).fetchone()
                    if row:
                        goal_id = row[0]
            except Exception:
                goal_id = None
        return adapter.run_capability(
            project_name,
            operation,
            task_id=task_id,
            goal_id=goal_id,
        )

    # Legacy direct gateway (adapter not deployed).
    proc = subprocess.run(
        [
            sys.executable, str(GATEWAY), 'run', operation,
            '--project', project_name,
            *(['--task-id', str(task_id)] if task_id is not None else []),
        ],
        capture_output=True, text=True, timeout=180, shell=False,
    )
    stdout = proc.stdout.strip()
    payload = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            lines = [x for x in stdout.splitlines() if x.strip()]
            for start in range(len(lines)):
                try:
                    payload = json.loads('\n'.join(lines[start:]))
                    break
                except json.JSONDecodeError:
                    continue
    return {
        'process_exit_code': proc.returncode,
        'result': payload,
        'stdout': stdout[-20000:],
        'stderr': proc.stderr[-12000:],
        'admitted': True,
    }


def gateway_ok(run):
    if run.get('admitted') is False:
        return False
    result = run.get('result') or {}
    return run.get('process_exit_code') == 0 and result.get('ok') is True



def execution_context(task_id=None):
    """Return effective task/run/attempt context for mutation events."""
    effective_task_id = task_id

    if effective_task_id is None:
        raw_task_id = os.environ.get("ANU_TASK_ID")
        if raw_task_id:
            try:
                effective_task_id = int(raw_task_id)
            except ValueError:
                effective_task_id = None

    run_id = os.environ.get("ANU_RUN_ID") or None

    attempt = None
    raw_attempt = os.environ.get("ANU_ATTEMPT")
    if raw_attempt:
        try:
            attempt = int(raw_attempt)
        except ValueError:
            attempt = None

    goal_id = None
    parent_task_id = None
    if effective_task_id is not None:
        try:
            with _db_helper().db_connect(DB_FILE) as db:
                row = db.execute(
                    "SELECT goal_id, parent_task_id FROM tasks WHERE id = ?",
                    (effective_task_id,),
                ).fetchone()
                if row:
                    goal_id, parent_task_id = row[0], row[1]
        except Exception:
            pass

    return effective_task_id, goal_id, parent_task_id, run_id, attempt


def ensure_column(db, table, column, definition):
    cols = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
    if column not in cols:
        db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def ensure_state():
    CHECKPOINT_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _db_helper().db_connect(DB_FILE) as db:
        # Preserve the user's existing schema and add only missing audit fields.
        ensure_column(db, 'mutation_transactions', 'transaction_id', 'TEXT')
        ensure_column(db, 'mutation_transactions', 'task_id', 'INTEGER')
        ensure_column(db, 'mutation_transactions', 'files_changed', 'INTEGER')
        ensure_column(db, 'mutation_transactions', 'baseline_sha256', 'TEXT')
        ensure_column(db, 'mutation_transactions', 'rollback_sha256', 'TEXT')
        ensure_column(db, 'mutation_transactions', 'integrity_verified', 'INTEGER')
        ensure_column(db, 'mutation_transactions', 'completed_at', 'TEXT')
        ensure_column(db, 'mutation_transactions', 'idempotency_key', 'TEXT')
        db.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_mutation_transactions_transaction_id '
            'ON mutation_transactions(transaction_id)'
        )
        db.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_mutation_transactions_committed_key '
            'ON mutation_transactions(idempotency_key) '
            "WHERE status = 'COMMITTED' AND idempotency_key IS NOT NULL"
        )
        db.commit()


def idempotency_key(task_id, project_name, operation, changes):
    # Without a task there is no unit of work to deduplicate.
    if task_id is None:
        return None
    payload = {
        'task_id': task_id,
        'project': project_name,
        'operation': operation,
        'changes': [
            {'file': c['file'], 'old': c['old'], 'new': c['new']}
            for c in changes
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def find_committed_transaction(key):
    if key is None:
        return None
    with _db_helper().db_connect(DB_FILE) as db:
        row = db.execute(
            """SELECT transaction_id FROM mutation_transactions
               WHERE idempotency_key = ? AND status = 'COMMITTED'
               ORDER BY id LIMIT 1""",
            (key,),
        ).fetchone()
    return row[0] if row else None


def checkpoint_event(transaction_id, task_id, phase, state, metadata):
    context_task_id, goal_id, parent_task_id, run_id, attempt = execution_context()
    task_id = task_id if task_id is not None else context_task_id

    event_metadata = dict(metadata or {})
    if task_id is not None:
        event_metadata["task_id"] = task_id
    if run_id is not None:
        event_metadata["run_id"] = run_id
    if attempt is not None:
        event_metadata["attempt"] = attempt

    with _db_helper().db_connect(DB_FILE) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            """INSERT INTO checkpoints(task_id, phase, state, created_at)
               VALUES (?, ?, ?, ?)""",
            (task_id, f"mutation:{transaction_id}:{phase}", state, now_iso()),
        )
        db.execute(
            """INSERT INTO agent_events
               (task_id, goal_id, parent_task_id, run_id, attempt,
                event_type, message, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                goal_id,
                parent_task_id,
                run_id,
                attempt,
                "MUTATION_CHECKPOINT",
                f"{transaction_id}: {state}",
                json.dumps(event_metadata, sort_keys=True),
                now_iso(),
            ),
        )
        db.commit()

TX_COLUMNS = (
    'project', 'file_path', 'operation', 'status', 'baseline_ok',
    'mutation_test_ok', 'rollback_attempted', 'rollback_verified',
    'checkpoint_path', 'details', 'created_at', 'transaction_id',
    'task_id', 'files_changed', 'baseline_sha256', 'rollback_sha256',
    'integrity_verified', 'completed_at', 'idempotency_key',
)


def tx_values(tx):
    return (
        tx["project"],
        tx["primary_file"],
        tx["operation"],
        tx["status"],
        int(tx["baseline_ok"]),
        int(tx["mutation_test_ok"]),
        int(tx["rollback_attempted"]),
        int(tx["rollback_verified"]),
        tx["checkpoint_path"],
        json.dumps(tx["details"], sort_keys=True),
        tx["created_at"],
        tx["transaction_id"],
        tx.get("task_id"),
        len(tx["files"]),
        tx["baseline_sha256"],
        tx.get("rollback_sha256"),
        int(tx["integrity_verified"]),
        tx.get("completed_at"),
        tx.get("idempotency_key"),
    )


def insert_transaction(db, tx):
    db.execute(
        f"""INSERT INTO mutation_transactions ({', '.join(TX_COLUMNS)})
            VALUES ({', '.join('?' for _ in TX_COLUMNS)})""",
        tx_values(tx),
    )


def record_started(tx):
    """Write-ahead record: every transaction is visible before any file changes."""
    with _db_helper().db_connect(DB_FILE) as db:
        insert_transaction(db, tx)
        db.commit()


def persist(tx):
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(tx, sort_keys=True) + "\n")

    with _db_helper().db_connect(DB_FILE) as db:
        updated = db.execute(
            f"""UPDATE mutation_transactions
                SET {', '.join(f'{c} = ?' for c in TX_COLUMNS)}
                WHERE transaction_id = ?""",
            (*tx_values(tx), tx["transaction_id"]),
        )
        if updated.rowcount == 0:
            insert_transaction(db, tx)

        context_task_id, goal_id, parent_task_id, run_id, attempt = execution_context()
        task_id = (
            tx.get("task_id")
            if tx.get("task_id") is not None
            else context_task_id
        )

        event_metadata = {
            "transaction_id": tx["transaction_id"],
            "project": tx["project"],
            "files": [f["path"] for f in tx["files"]],
            "status": tx["status"],
        }
        if task_id is not None:
            event_metadata["task_id"] = task_id
        if run_id is not None:
            event_metadata["run_id"] = run_id
        if attempt is not None:
            event_metadata["attempt"] = attempt

        if goal_id is not None:
            event_metadata["goal_id"] = goal_id
        if parent_task_id is not None:
            event_metadata["parent_task_id"] = parent_task_id

        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            """INSERT INTO agent_events
               (task_id, goal_id, parent_task_id, run_id, attempt,
                event_type, message, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                goal_id,
                parent_task_id,
                run_id,
                attempt,
                "MUTATION_TRANSACTION",
                f"{tx['transaction_id']}: {tx['status']}",
                json.dumps(event_metadata, sort_keys=True),
                now_iso(),
            ),
        )
        db.commit()

def load_changes(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception as exc:
        fail(f'Unable to read changes file: {exc}', 2)
    if not isinstance(data, list) or not data:
        fail('Changes file must contain a non-empty JSON array.', 2)
    normalized = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            fail(f'Change #{idx + 1} must be an object.', 2)
        for key in ('file', 'old', 'new'):
            if key not in item or not isinstance(item[key], str):
                fail(f'Change #{idx + 1} requires string field: {key}', 2)
        normalized.append(item)
    return normalized


def make_checkpoint(project_name, project, changes, transaction_id):
    checkpoint_dir = CHECKPOINT_ROOT / project_name / transaction_id
    checkpoint_dir.mkdir(parents=True, exist_ok=False)
    files = []
    seen = set()
    for change in changes:
        rel = change['file']
        if rel in seen:
            raise RuntimeError(f'Duplicate file in transaction: {rel}')
        seen.add(rel)
        target = resolve_file(project, rel)
        original_bytes = target.read_bytes()
        original_text = original_bytes.decode('utf-8')
        count = original_text.count(change['old'])
        if count != 1:
            raise RuntimeError(f'{rel}: expected exactly one match, found {count}')
        cp = checkpoint_dir / rel
        cp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, cp)
        files.append({
            'path': rel,
            'checkpoint': str(cp),
            'baseline_sha256': sha256_file(target),
            'mode': target.stat().st_mode,
            'old': change['old'],
            'new': change['new'],
        })
    manifest = {
        'transaction_id': transaction_id,
        'project': project_name,
        'created_at': now_iso(),
        'files': files,
    }
    (checkpoint_dir / 'manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8'
    )
    return checkpoint_dir, files


def rollback_files(files, project):
    for item in files:
        target = resolve_file(project, item['path'])
        cp = Path(item['checkpoint'])

        if not cp.is_file():
            raise RuntimeError(f'Missing checkpoint: {cp}')

        expected_sha256 = item.get('baseline_sha256')
        if expected_sha256:
            checkpoint_sha256 = sha256_file(cp)
            if checkpoint_sha256 != expected_sha256:
                raise RuntimeError(
                    f"Checkpoint integrity mismatch: {cp} "
                    f"(expected {expected_sha256}, "
                    f"got {checkpoint_sha256})"
                )

        shutil.copy2(cp, target)
        os.chmod(target, item['mode'])


def main():
    with shared_execution_lock():
        return run_transaction()


def run_transaction():
    parser = argparse.ArgumentParser(description='ANU Safe Mutation Engine v0.2')
    parser.add_argument('--project', required=True)
    parser.add_argument('--changes-file', required=True,
                        help='JSON array: [{"file":"...","old":"...","new":"..."}]')
    parser.add_argument('--task-id', type=int, default=None)
    parser.add_argument('--operation', default='python_test',
                        choices=['python_test', 'python_compile'])
    parser.add_argument(
        '--production',
        action='store_true',
        help='V0.7.0: treat as production deployment (approval gate)',
    )
    args = parser.parse_args()

    ensure_state()
    project = resolve_project(args.project)
    changes_path = resolve_changes_file(project, args.changes_file)
    changes = load_changes(changes_path)
    transaction_id = f'mut-{utc_stamp()}-{uuid.uuid4().hex[:8]}'

    # V0.7.0: production gate before any write-ahead / file touch.
    if args.production:
        _gate_production_deployment(
            task_id=args.task_id,
            proposed_change=str(changes_path),
        )

    tx = {
        'transaction_id': transaction_id,
        'task_id': args.task_id,
        'created_at': now_iso(),
        'completed_at': None,
        'project': args.project,
        'primary_file': changes[0]['file'],
        'files': [{'path': x['file']} for x in changes],
        'operation': args.operation,
        'status': 'STARTED',
        'baseline_ok': False,
        'mutation_test_ok': False,
        'rollback_attempted': False,
        'rollback_verified': False,
        'integrity_verified': False,
        'baseline_sha256': None,
        'rollback_sha256': None,
        'checkpoint_path': None,
        'details': {},
        'idempotency_key': idempotency_key(
            args.task_id, args.project, args.operation, changes
        ),
    }

    # Outside the try: without a write-ahead record no file may be touched.
    record_started(tx)

    try:
        committed = find_committed_transaction(tx['idempotency_key'])
        if committed:
            tx['status'] = 'ALREADY_COMMITTED'
            tx['integrity_verified'] = True
            tx['details']['committed_transaction_id'] = committed
            return finalize(tx, 0)

        baseline = run_gateway(args.project, args.operation, args.task_id)
        tx['details']['baseline'] = baseline
        tx['baseline_ok'] = gateway_ok(baseline)
        if not tx['baseline_ok']:
            tx['status'] = 'ABORTED_BASELINE_FAILURE'
            return_code = 2
            return finalize(tx, return_code)

        # Verify-only / already-applied: old==new for every entry. Inspect/test
        # stages share the changes file after implement; noop must COMMIT.
        verify_only = all(c['old'] == c['new'] for c in changes)
        tx['details']['verify_only'] = verify_only

        checkpoint_dir, files = make_checkpoint(
            args.project, project, changes, transaction_id
        )
        tx['files'] = files
        tx['checkpoint_path'] = str(checkpoint_dir)
        tx['baseline_sha256'] = hashlib.sha256(
            json.dumps([(f['path'], f['baseline_sha256']) for f in files]).encode()
        ).hexdigest()
        checkpoint_event(transaction_id, args.task_id, 'PRE_MUTATION',
                         'CHECKPOINT_CREATED', {'files': files})

        if not verify_only:
            for item, change in zip(files, changes):
                target = resolve_file(project, item['path'])
                current = ensure_text(target)
                if current.count(change['old']) != 1:
                    raise RuntimeError(
                        f"{change['file']}: source changed after checkpoint creation"
                    )
                atomic_write(target, current.replace(change['old'], change['new'], 1))

        checkpoint_event(transaction_id, args.task_id, 'MUTATION',
                         'MUTATION_APPLIED' if not verify_only else 'VERIFY_ONLY',
                         {'files': [f['path'] for f in files],
                          'verify_only': verify_only})
        mutation_test = run_gateway(args.project, args.operation, args.task_id)
        tx['details']['mutation_test'] = mutation_test
        tx['mutation_test_ok'] = gateway_ok(mutation_test)

        # V0.6.5: post-mutation digests + diff-scope before COMMIT.
        post_shas = {
            f['path']: sha256_file(resolve_file(project, f['path']))
            for f in files
        }
        tx['details']['post_mutation_sha256'] = post_shas
        tx['details']['baseline_sha256_by_file'] = {
            f['path']: f['baseline_sha256'] for f in files
        }
        for item in files:
            item['after_sha256'] = post_shas[item['path']]
        tx['files'] = files

        actual_changed = _collect_actual_changed_files(
            args.project, args.task_id, tx
        )
        tx['actual_changed_files'] = actual_changed
        scope_ok, extras = _diff_scope_ok(
            [f['path'] for f in files], actual_changed
        )
        if verify_only:
            intended_ok = True
        else:
            intended_ok = all(
                post_shas[f['path']] != f['baseline_sha256'] for f in files
            )
        tx['details']['verification_scope'] = {
            'scope_ok': scope_ok,
            'extras': extras,
            'intended_ok': intended_ok,
            'verify_only': verify_only,
        }

        if tx['mutation_test_ok'] and scope_ok and intended_ok:
            tx['status'] = 'COMMITTED'
            tx['integrity_verified'] = True
            checkpoint_event(transaction_id, args.task_id, 'POST_MUTATION',
                             'COMMITTED', {
                                 'files': [f['path'] for f in files],
                                 'actual_changed_files': actual_changed,
                             })
            return finalize(tx, 0)

        # Tests failed, unintended change, or no intended change → rollback.
        if tx['mutation_test_ok'] and (not scope_ok or not intended_ok):
            tx['details']['rollback_reason'] = (
                'unintended_change' if not scope_ok else 'no_intended_change'
            )

        tx['rollback_attempted'] = True
        checkpoint_event(transaction_id, args.task_id, 'ROLLBACK',
                         'ROLLBACK_STARTED', {
                             'reason': tx['details'].get('rollback_reason'),
                             'extras': extras,
                         })
        rollback_files(files, project)

        rollback_shas = {f['path']: sha256_file(resolve_file(project, f['path'])) for f in files}
        tx['details']['rollback_sha256'] = rollback_shas
        tx['rollback_sha256'] = hashlib.sha256(
            json.dumps(sorted(rollback_shas.items())).encode()
        ).hexdigest()
        tx['integrity_verified'] = all(
            rollback_shas[f['path']] == f['baseline_sha256'] for f in files
        )

        rollback_test = run_gateway(args.project, args.operation, args.task_id)
        tx['details']['rollback_test'] = rollback_test
        tx['rollback_verified'] = gateway_ok(rollback_test)

        if tx['integrity_verified'] and tx['rollback_verified']:
            tx['status'] = 'ROLLED_BACK'
            checkpoint_event(transaction_id, args.task_id, 'ROLLBACK',
                             'ROLLBACK_VERIFIED', {
                                 'integrity_verified': True,
                                 'test_verified': True,
                             })
            return finalize(tx, 1)

        tx['status'] = 'ROLLBACK_VERIFICATION_FAILED'
        checkpoint_event(transaction_id, args.task_id, 'ROLLBACK',
                         'ROLLBACK_VERIFICATION_FAILED', {
                             'integrity_verified': tx['integrity_verified'],
                             'test_verified': tx['rollback_verified'],
                         })
        return finalize(tx, 4)

    except Exception as exc:
        tx['status'] = 'MUTATION_ENGINE_ERROR'
        tx['details']['exception'] = str(exc)
        return finalize(tx, 10)


def _collect_actual_changed_files(project_name, task_id, tx):
    """Best-effort working-tree change list via git status --short.

    V0.8.11: if the project is not itself a git root, skip status. Walking up
    to ``ANU_BASE`` reports the whole agent checkout and poisons diff-scope.
    Safe Mutation only writes declared files, so ``None`` remains scope-OK.
    """
    project_root = WORKSPACE / project_name
    git_meta = project_root / '.git'
    if not git_meta.exists():
        tx['details']['git_status_short_skipped'] = 'project_not_git_root'
        return None
    try:
        payload = run_gateway(project_name, 'git_status_short', task_id)
    except Exception as exc:
        tx['details']['git_status_short_error'] = str(exc)
        return None
    tx['details']['git_status_short'] = payload
    stdout = ''
    if isinstance(payload, dict):
        result = payload.get('result')
        if isinstance(result, dict):
            stdout = result.get('stdout') or ''
        if not stdout:
            stdout = payload.get('stdout') or ''
        nested = payload.get('payload')
        if not stdout and isinstance(nested, dict):
            stdout = nested.get('stdout') or ''
    paths = _parse_git_status_short(stdout)
    # Reject parent-repo relative escapes if status still walks upward.
    if any('..' in str(p).replace('\\', '/').split('/') for p in paths):
        tx['details']['git_status_short_skipped'] = 'paths_outside_project'
        return None
    return paths


def _parse_git_status_short(stdout):
    paths = []
    if not stdout:
        return paths
    for line in str(stdout).splitlines():
        if not line.strip():
            continue
        body = line[3:] if len(line) >= 3 and line[2] == ' ' else line.strip()
        if ' -> ' in body:
            body = body.split(' -> ', 1)[1]
        body = body.strip().strip('"')
        if body:
            paths.append(body.replace('\\', '/'))
    return paths


# V0.9: progress file is written by the autonomy controller, not mutations.
_SCOPE_IGNORE_NAMES = frozenset({
    'AUTONOMY_PROGRESS.md',
    '.anu-progress.tmp',
})


def _diff_scope_ok(declared_files, actual_changed_files):
    if actual_changed_files is None:
        # No listing available (non-git workspace): Safe Mutation only
        # wrote declared files, so scope is OK.
        return True, []
    declared = {str(p).replace('\\', '/') for p in declared_files}
    extras = sorted(
        p for p in actual_changed_files
        if str(p).replace('\\', '/') not in declared
        and Path(str(p).replace('\\', '/')).name not in _SCOPE_IGNORE_NAMES
        and not str(p).replace('\\', '/').endswith('AUTONOMY_PROGRESS.md')
    )
    return len(extras) == 0, extras


def finalize(tx, code):
    tx['completed_at'] = now_iso()
    persist(tx)
    file_records = []
    for f in tx['files']:
        if isinstance(f, dict):
            file_records.append({
                'path': f.get('path'),
                'baseline_sha256': f.get('baseline_sha256'),
                'after_sha256': f.get('after_sha256'),
            })
    print(json.dumps({
        'ok': tx['status'] in {'COMMITTED', 'ALREADY_COMMITTED', 'ROLLED_BACK'},
        'transaction_id': tx['transaction_id'],
        'idempotency_key': tx.get('idempotency_key'),
        'project': tx['project'],
        'operation': tx.get('operation'),
        'files': [f['path'] for f in tx['files']],
        'file_records': file_records,
        'actual_changed_files': tx.get('actual_changed_files'),
        'status': tx['status'],
        'baseline_ok': tx['baseline_ok'],
        'mutation_test_ok': tx['mutation_test_ok'],
        'rollback_attempted': tx['rollback_attempted'],
        'rollback_verified': tx['rollback_verified'],
        'integrity_verified': tx['integrity_verified'],
        'checkpoint_path': tx['checkpoint_path'],
        'details': tx['details'],
    }, indent=2))
    return_code = code
    raise SystemExit(return_code)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(json.dumps({'ok': False, 'error': 'Interrupted'}, indent=2))
        sys.exit(130)
    except subprocess.TimeoutExpired as exc:
        print(json.dumps({'ok': False, 'error': f'Gateway timeout: {exc}'}, indent=2))
        sys.exit(5)
    except SystemExit:
        raise
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, indent=2))
        sys.exit(10)
