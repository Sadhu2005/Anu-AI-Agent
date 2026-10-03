#!/usr/bin/env python3

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/srv/anu')
GATEWAY = BASE / 'bin' / 'anu-command-gateway.py'
DB_FILE = BASE / 'brain' / 'anu-brain.db'
LOG_FILE = BASE / 'logs' / 'anu-mutations.jsonl'
CHECKPOINT_ROOT = BASE / 'checkpoints' / 'mutations'
WORKSPACE = BASE / 'workspace' / 'projects'


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def fail(message, code=1):
    print(json.dumps({'ok': False, 'error': message}, indent=2))
    raise SystemExit(code)


def resolve_project(project_name):
    if not project_name:
        fail('Project is required.')
    p = Path(project_name)
    if p.is_absolute() or '..' in p.parts:
        fail('Invalid project path.')
    project = (WORKSPACE / p).resolve()
    workspace = WORKSPACE.resolve()
    try:
        project.relative_to(workspace)
    except ValueError:
        fail('Project is outside ANU workspace.')
    if not project.is_dir():
        fail(f'Project does not exist: {project_name}')
    return project


def resolve_file(project, relative_file):
    p = Path(relative_file)
    if p.is_absolute() or '..' in p.parts:
        fail('Invalid target file path.')
    target = (project / p).resolve()
    try:
        target.relative_to(project.resolve())
    except ValueError:
        fail('Target file is outside project.')
    if not target.is_file():
        fail(f'Target file does not exist: {relative_file}')
    return target


def run_gateway(project_name, operation):
    proc = subprocess.run(
        [
            sys.executable,
            str(GATEWAY),
            'run',
            operation,
            '--project',
            project_name,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        shell=False,
    )

    payload = None
    stdout = proc.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            # Keep a useful diagnostic even if a future gateway version emits
            # extra text around the JSON response.
            lines = [line for line in stdout.splitlines() if line.strip()]
            for start in range(len(lines)):
                candidate = '\n'.join(lines[start:])
                try:
                    payload = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue

    return {
        'process_exit_code': proc.returncode,
        'result': payload,
        'stdout': stdout[-20000:],
        'stderr': proc.stderr[-12000:],
    }


def ensure_state():
    CHECKPOINT_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_FILE) as db:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS mutation_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                file_path TEXT NOT NULL,
                operation TEXT NOT NULL,
                status TEXT NOT NULL,
                baseline_ok INTEGER,
                mutation_test_ok INTEGER,
                rollback_attempted INTEGER,
                rollback_verified INTEGER,
                checkpoint_path TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.commit()


def persist(transaction):
    encoded = json.dumps(transaction, sort_keys=True)
    with LOG_FILE.open('a', encoding='utf-8') as f:
        f.write(encoded + '\n')

    with sqlite3.connect(DB_FILE) as db:
        db.execute(
            '''
            INSERT INTO mutation_transactions
            (project, file_path, operation, status, baseline_ok,
             mutation_test_ok, rollback_attempted, rollback_verified,
             checkpoint_path, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                transaction['project'],
                transaction['file_path'],
                transaction['operation'],
                transaction['status'],
                int(bool(transaction.get('baseline_ok', False))),
                int(bool(transaction.get('mutation_test_ok', False))),
                int(bool(transaction.get('rollback_attempted', False))),
                int(bool(transaction.get('rollback_verified', False))),
                transaction.get('checkpoint_path'),
                json.dumps(transaction.get('details', {}), sort_keys=True),
                transaction['created_at'],
            ),
        )
        db.commit()


def atomic_write(path, content):
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        'w',
        encoding='utf-8',
        dir=str(path.parent),
        prefix=f'.{path.name}.anu-',
        delete=False,
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


def main():
    parser = argparse.ArgumentParser(description='ANU Safe Mutation Engine v0.1')
    parser.add_argument('--project', required=True)
    parser.add_argument('--file', required=True)
    parser.add_argument('--old', required=True)
    parser.add_argument('--new', required=True)
    parser.add_argument('--operation', default='python_test', choices=['python_test', 'python_compile'])
    args = parser.parse_args()

    ensure_state()
    project = resolve_project(args.project)
    target = resolve_file(project, args.file)

    transaction = {
        'created_at': now_iso(),
        'project': args.project,
        'file_path': args.file,
        'operation': args.operation,
        'status': 'STARTED',
        'baseline_ok': False,
        'mutation_test_ok': False,
        'rollback_attempted': False,
        'rollback_verified': False,
        'checkpoint_path': None,
        'details': {},
    }

    # Gate 1: baseline must pass before we mutate anything.
    baseline = run_gateway(args.project, args.operation)
    baseline_result = baseline.get('result') or {}
    baseline_ok = (
        baseline['process_exit_code'] == 0
        and baseline_result.get('ok') is True
    )
    transaction['baseline_ok'] = baseline_ok
    transaction['details']['baseline'] = baseline

    if not baseline_ok:
        transaction['status'] = 'ABORTED_BASELINE_FAILURE'
        persist(transaction)
        print(json.dumps(transaction, indent=2))
        return 2

    original = target.read_text(encoding='utf-8')
    count = original.count(args.old)
    if count != 1:
        transaction['status'] = 'ABORTED_AMBIGUOUS_REPLACEMENT'
        transaction['details']['replacement_matches'] = count
        persist(transaction)
        print(json.dumps(transaction, indent=2))
        return 3

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    checkpoint_dir = CHECKPOINT_ROOT / args.project / stamp
    checkpoint_file = checkpoint_dir / args.file
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, checkpoint_file)
    transaction['checkpoint_path'] = str(checkpoint_file)

    manifest = {
        'created_at': transaction['created_at'],
        'project': args.project,
        'file_path': args.file,
        'checkpoint_file': str(checkpoint_file),
        'sha256_note': 'v0.1 intentionally records the checkpoint path; content integrity will be added in v0.2',
    }
    (checkpoint_dir / 'manifest.json').write_text(
        json.dumps(manifest, indent=2) + '\n',
        encoding='utf-8',
    )

    mutated = original.replace(args.old, args.new, 1)
    atomic_write(target, mutated)

    # Gate 2: test the mutation.
    mutation_test = run_gateway(args.project, args.operation)
    mutation_result = mutation_test.get('result') or {}
    mutation_ok = (
        mutation_test['process_exit_code'] == 0
        and mutation_result.get('ok') is True
    )
    transaction['mutation_test_ok'] = mutation_ok
    transaction['details']['mutation_test'] = mutation_test

    if mutation_ok:
        transaction['status'] = 'COMMITTED'
        persist(transaction)
        print(json.dumps(transaction, indent=2))
        return 0

    # Gate 3: rollback exact touched file.
    transaction['rollback_attempted'] = True
    shutil.copy2(checkpoint_file, target)

    # Gate 4: verify rollback by re-running the same test.
    rollback_test = run_gateway(args.project, args.operation)
    rollback_result = rollback_test.get('result') or {}
    rollback_ok = (
        rollback_test['process_exit_code'] == 0
        and rollback_result.get('ok') is True
    )
    transaction['rollback_verified'] = rollback_ok
    transaction['details']['rollback_test'] = rollback_test

    if rollback_ok:
        transaction['status'] = 'ROLLED_BACK'
        persist(transaction)
        print(json.dumps(transaction, indent=2))
        return 1

    transaction['status'] = 'ROLLBACK_VERIFICATION_FAILED'
    persist(transaction)
    print(json.dumps(transaction, indent=2))
    return 4


if __name__ == '__main__':
    try:
        main()
    except subprocess.TimeoutExpired as exc:
        print(json.dumps({'ok': False, 'error': f'Gateway timeout: {exc}'}, indent=2))
        sys.exit(5)
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, indent=2))
        sys.exit(10)
