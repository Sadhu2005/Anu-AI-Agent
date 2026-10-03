#!/usr/bin/env python3
"""ANU Task Engine v0.3

Orchestrates task lifecycle while delegating filesystem changes and code
verification to the existing Safe Mutation Engine. It never executes raw
shell commands itself.
"""

import argparse
import json
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/srv/anu')
DB_FILE = BASE / 'brain' / 'anu-brain.db'
SAFE_MUTATION = BASE / 'bin' / 'anu-safe-mutation-v0.2.py'
LOG_FILE = BASE / 'logs' / 'anu-task-engine.jsonl'

TERMINAL = {'completed', 'failed', 'blocked'}
RUNNABLE = {'pending', 'ready', 'failed'}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def event(task_id, event_type, message, metadata=None):
    record = {
        'timestamp': now_iso(),
        'agent': 'ANU',
        'task_id': task_id,
        'event_type': event_type,
        'message': message,
        'metadata': metadata or {},
    }
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, sort_keys=True) + '\n')

    try:
        with sqlite3.connect(DB_FILE) as db:
            db.execute(
                '''INSERT INTO agent_events(event_type, message, metadata, created_at)
                   VALUES (?, ?, ?, ?)''',
                (
                    event_type,
                    message,
                    json.dumps(metadata or {}, sort_keys=True),
                    now_iso(),
                ),
            )
            db.commit()
    except Exception:
        pass


def db_connect():
    return sqlite3.connect(DB_FILE)


def fetch_task(task_id):
    with db_connect() as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            '''SELECT id, goal_id, parent_task_id, task_key, title, description,
                      status, priority, attempts, created_at, updated_at
               FROM tasks WHERE id = ?''',
            (task_id,),
        ).fetchone()
    return dict(row) if row else None


def update_task(task_id, status=None, attempts=None):
    fields = []
    values = []
    if status is not None:
        fields.append('status = ?')
        values.append(status)
    if attempts is not None:
        fields.append('attempts = ?')
        values.append(attempts)
    fields.append('updated_at = ?')
    values.append(now_iso())
    values.append(task_id)

    with db_connect() as db:
        db.execute(
            f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        db.commit()


def create_task(title, description, priority, task_key=None,
                goal_id=None, parent_task_id=None):
    task_key = task_key or f'TASK-{uuid.uuid4().hex[:10].upper()}'
    created = now_iso()
    with db_connect() as db:
        existing = db.execute(
            'SELECT id FROM tasks WHERE task_key = ?', (task_key,)
        ).fetchone()
        if existing:
            raise RuntimeError(f'Task key already exists: {task_key}')
        cur = db.execute(
            '''INSERT INTO tasks(
                   goal_id, parent_task_id, task_key, title, description,
                   status, priority, attempts, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?)''',
            (
                goal_id,
                parent_task_id,
                task_key,
                title,
                description,
                priority,
                created,
                created,
            ),
        )
        task_id = cur.lastrowid
        db.commit()

    event(task_id, 'TASK_CREATED', f'{task_key}: pending', {
        'task_key': task_key,
        'title': title,
        'priority': priority,
        'goal_id': goal_id,
        'parent_task_id': parent_task_id,
    })
    return fetch_task(task_id)


def parse_json_output(stdout):
    stdout = (stdout or '').strip()
    if not stdout:
        return None
    try:
        value = json.loads(stdout)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        lines = [line for line in stdout.splitlines() if line.strip()]
        for i in range(len(lines)):
            try:
                value = json.loads('\n'.join(lines[i:]))
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                continue
    return None


def classify_mutation(result, process_code):
    status = (result or {}).get('status')
    if process_code == 0 and status == 'COMMITTED':
        return 'completed'
    if status == 'ROLLED_BACK':
        return 'failed'
    return 'blocked'


def run_task(task_id, project, changes_file, operation):
    task = fetch_task(task_id)
    if not task:
        raise RuntimeError(f'Task not found: {task_id}')

    if task['status'] not in RUNNABLE:
        raise RuntimeError(
            f'Task {task_id} is not runnable from status={task["status"]}'
        )

    attempt = int(task['attempts']) + 1
    update_task(task_id, status='planning', attempts=attempt)
    event(task_id, 'TASK_STATE', 'planning', {'attempt': attempt})

    # v0.3 currently takes an already-produced safe mutation plan. Planning is
    # therefore explicit but deterministic; a future planner can populate the
    # changes file before this execution step.
    update_task(task_id, status='ready')
    event(task_id, 'TASK_STATE', 'ready', {
        'project': project,
        'changes_file': changes_file,
        'operation': operation,
    })

    update_task(task_id, status='running')
    event(task_id, 'TASK_STATE', 'running', {'attempt': attempt})

    command = [
        sys.executable,
        str(SAFE_MUTATION),
        '--project', project,
        '--changes-file', changes_file,
        '--task-id', str(task_id),
        '--operation', operation,
    ]

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        update_task(task_id, status='blocked')
        event(task_id, 'TASK_BLOCKED', 'Safe Mutation Engine timed out', {
            'attempt': attempt,
            'timeout_seconds': 300,
            'stdout': (exc.stdout or '')[-8000:] if exc.stdout else '',
            'stderr': (exc.stderr or '')[-8000:] if exc.stderr else '',
        })
        return {
            'ok': False,
            'task_id': task_id,
            'status': 'blocked',
            'reason': 'mutation_engine_timeout',
        }, 5

    result = parse_json_output(process.stdout)
    if result is None:
        update_task(task_id, status='blocked')
        event(task_id, 'TASK_BLOCKED', 'Invalid Safe Mutation response', {
            'attempt': attempt,
            'process_exit_code': process.returncode,
            'stdout': process.stdout[-12000:],
            'stderr': process.stderr[-8000:],
        })
        return {
            'ok': False,
            'task_id': task_id,
            'status': 'blocked',
            'reason': 'invalid_mutation_engine_response',
        }, 6

    update_task(task_id, status='verifying')
    event(task_id, 'TASK_STATE', 'verifying', {
        'attempt': attempt,
        'transaction_id': result.get('transaction_id'),
        'mutation_status': result.get('status'),
    })

    final_status = classify_mutation(result, process.returncode)
    update_task(task_id, status=final_status)

    event_type = {
        'completed': 'TASK_COMPLETED',
        'failed': 'TASK_FAILED',
        'blocked': 'TASK_BLOCKED',
    }[final_status]
    event(task_id, event_type, f'task {final_status}', {
        'attempt': attempt,
        'transaction_id': result.get('transaction_id'),
        'mutation_status': result.get('status'),
        'baseline_ok': result.get('baseline_ok'),
        'mutation_test_ok': result.get('mutation_test_ok'),
        'rollback_attempted': result.get('rollback_attempted'),
        'rollback_verified': result.get('rollback_verified'),
        'integrity_verified': result.get('integrity_verified'),
    })

    return {
        'ok': final_status == 'completed',
        'task_id': task_id,
        'task_status': final_status,
        'attempt': attempt,
        'mutation': result,
        'engine_exit_code': process.returncode,
        'engine_stderr': process.stderr[-8000:],
    }, (0 if final_status == 'completed' else process.returncode or 1)


def list_tasks(limit):
    with db_connect() as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            '''SELECT id, task_key, title, status, priority, attempts,
                      created_at, updated_at
               FROM tasks ORDER BY id DESC LIMIT ?''',
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def main():
    parser = argparse.ArgumentParser(description='ANU Task Engine v0.3')
    sub = parser.add_subparsers(dest='command', required=True)

    create = sub.add_parser('create')
    create.add_argument('--title', required=True)
    create.add_argument('--description', default='')
    create.add_argument('--priority', type=int, default=5)
    create.add_argument('--task-key')
    create.add_argument('--goal-id', type=int)
    create.add_argument('--parent-task-id', type=int)

    run = sub.add_parser('run')
    run.add_argument('--task-id', type=int, required=True)
    run.add_argument('--project', required=True)
    run.add_argument('--changes-file', required=True)
    run.add_argument('--operation', choices=['python_test', 'python_compile'],
                     default='python_test')

    show = sub.add_parser('show')
    show.add_argument('--task-id', type=int, required=True)

    ls = sub.add_parser('list')
    ls.add_argument('--limit', type=int, default=20)

    args = parser.parse_args()

    if args.command == 'create':
        result = create_task(
            args.title,
            args.description,
            args.priority,
            args.task_key,
            args.goal_id,
            args.parent_task_id,
        )
        print(json.dumps({'ok': True, 'task': result}, indent=2))
        return 0

    if args.command == 'show':
        result = fetch_task(args.task_id)
        if not result:
            print(json.dumps({'ok': False, 'error': 'Task not found'}, indent=2))
            return 1
        print(json.dumps({'ok': True, 'task': result}, indent=2))
        return 0

    if args.command == 'list':
        if args.limit < 1 or args.limit > 200:
            raise RuntimeError('--limit must be between 1 and 200')
        print(json.dumps({'ok': True, 'tasks': list_tasks(args.limit)}, indent=2))
        return 0

    if args.command == 'run':
        result, code = run_task(
            args.task_id,
            args.project,
            args.changes_file,
            args.operation,
        )
        print(json.dumps(result, indent=2))
        return code

    return 1


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(json.dumps({'ok': False, 'error': 'Interrupted'}, indent=2))
        sys.exit(130)
    except Exception as exc:
        print(json.dumps({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }, indent=2))
        sys.exit(1)
