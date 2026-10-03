#!/usr/bin/env python3
"""ANU Execution Context Propagation v0.5.1."""
from __future__ import annotations
import ast
import shutil
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/srv/anu/bin")
GATEWAY = BASE / "anu-command-gateway.py"
MUTATION = BASE / "anu-safe-mutation-v0.2.py"

def backup(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = path.with_name(f"{path.name}.backup-v0.5.1-context-{stamp}")
    shutil.copy2(path, out)
    return out

def replace_exact(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)

def validate(path: Path) -> None:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

GATEWAY_HELPER = '\ndef execution_context():\n    """Return optional task execution context from the inherited environment."""\n    task_id = None\n    run_id = None\n    attempt = None\n\n    raw_task_id = os.environ.get("ANU_TASK_ID")\n    raw_run_id = os.environ.get("ANU_RUN_ID")\n    raw_attempt = os.environ.get("ANU_ATTEMPT")\n\n    if raw_task_id:\n        try:\n            task_id = int(raw_task_id)\n        except ValueError:\n            task_id = None\n\n    if raw_run_id:\n        run_id = raw_run_id\n\n    if raw_attempt:\n        try:\n            attempt = int(raw_attempt)\n        except ValueError:\n            attempt = None\n\n    return task_id, run_id, attempt\n\n\n'
MUTATION_HELPER = '\ndef execution_context(task_id=None):\n    """Return effective task/run/attempt context for mutation events."""\n    effective_task_id = task_id\n\n    if effective_task_id is None:\n        raw_task_id = os.environ.get("ANU_TASK_ID")\n        if raw_task_id:\n            try:\n                effective_task_id = int(raw_task_id)\n            except ValueError:\n                effective_task_id = None\n\n    run_id = os.environ.get("ANU_RUN_ID") or None\n\n    attempt = None\n    raw_attempt = os.environ.get("ANU_ATTEMPT")\n    if raw_attempt:\n        try:\n            attempt = int(raw_attempt)\n        except ValueError:\n            attempt = None\n\n    goal_id = None\n    parent_task_id = None\n    if effective_task_id is not None:\n        try:\n            with sqlite3.connect(DB_FILE) as db:\n                row = db.execute(\n                    "SELECT goal_id, parent_task_id FROM tasks WHERE id = ?",\n                    (effective_task_id,),\n                ).fetchone()\n                if row:\n                    goal_id, parent_task_id = row\n        except Exception:\n            pass\n\n    return effective_task_id, goal_id, parent_task_id, run_id, attempt\n\n\n'

def patch_gateway() -> Path:
    text = GATEWAY.read_text(encoding="utf-8")
    marker = """def write_jsonl(record):
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\\n")


"""
    text = replace_exact(text, marker, marker + GATEWAY_HELPER, "gateway execution_context")
    text = replace_exact(text, 'def write_event(event_type, message, metadata=None):\n    timestamp = now()\n\n    write_jsonl({\n        "timestamp": timestamp,\n        "agent": "ANU",\n        "event_type": event_type,\n        "message": message,\n        "metadata": metadata or {},\n    })\n\n    try:\n        with sqlite3.connect(DB_FILE) as db:\n            db.execute(\n                """\n                INSERT INTO agent_events\n                (event_type, message, created_at)\n                VALUES (?, ?, ?)\n                """,\n                (\n                    event_type,\n                    message,\n                    datetime.now(timezone.utc).strftime(\n                        "%Y-%m-%d %H:%M:%S"\n                    ),\n                ),\n            )\n            db.commit()\n    except Exception:\n        pass\n\n\n', 'def write_event(event_type, message, metadata=None):\n    timestamp = now()\n    task_id, run_id, attempt = execution_context()\n\n    event_metadata = dict(metadata or {})\n    if task_id is not None:\n        event_metadata["task_id"] = task_id\n    if run_id is not None:\n        event_metadata["run_id"] = run_id\n    if attempt is not None:\n        event_metadata["attempt"] = attempt\n\n    write_jsonl({\n        "timestamp": timestamp,\n        "agent": "ANU",\n        "event_type": event_type,\n        "message": message,\n        "metadata": event_metadata,\n    })\n\n    try:\n        with sqlite3.connect(DB_FILE) as db:\n            goal_id = None\n            parent_task_id = None\n            if task_id is not None:\n                row = db.execute(\n                    "SELECT goal_id, parent_task_id FROM tasks WHERE id = ?",\n                    (task_id,),\n                ).fetchone()\n                if row:\n                    goal_id, parent_task_id = row\n\n            db.execute(\n                """\n                INSERT INTO agent_events\n                (task_id, goal_id, parent_task_id, run_id, attempt,\n                 event_type, message, metadata, created_at)\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\n                """,\n                (\n                    task_id,\n                    goal_id,\n                    parent_task_id,\n                    run_id,\n                    attempt,\n                    event_type,\n                    message,\n                    json.dumps(event_metadata, sort_keys=True),\n                    datetime.now(timezone.utc).strftime(\n                        "%Y-%m-%d %H:%M:%S"\n                    ),\n                ),\n            )\n            db.commit()\n    except Exception:\n        pass\n\n\n', "gateway write_event")
    out = backup(GATEWAY)
    GATEWAY.write_text(text, encoding="utf-8")
    validate(GATEWAY)
    return out

def patch_mutation() -> Path:
    text = MUTATION.read_text(encoding="utf-8")
    marker = """def gateway_ok(run):
    result = run.get('result') or {}
    return run.get('process_exit_code') == 0 and result.get('ok') is True


"""
    text = replace_exact(text, marker, marker + MUTATION_HELPER, "mutation execution_context")
    text = replace_exact(text, "def checkpoint_event(transaction_id, task_id, phase, state, metadata):\n    with sqlite3.connect(DB_FILE) as db:\n        db.execute(\n            '''INSERT INTO checkpoints(task_id, phase, state, created_at)\n               VALUES (?, ?, ?, ?)''',\n            (task_id, f'mutation:{transaction_id}:{phase}', state, now_iso()),\n        )\n        db.execute(\n            '''INSERT INTO agent_events(event_type, message, metadata, created_at)\n               VALUES (?, ?, ?, ?)''',\n            ('MUTATION_CHECKPOINT', f'{transaction_id}: {state}',\n             json.dumps(metadata, sort_keys=True), now_iso()),\n        )\n        db.commit()\n\n\n", 'def checkpoint_event(transaction_id, task_id, phase, state, metadata):\n    effective_task_id, goal_id, parent_task_id, run_id, attempt = (\n        execution_context(task_id)\n    )\n\n    event_metadata = dict(metadata or {})\n    if effective_task_id is not None:\n        event_metadata["task_id"] = effective_task_id\n    if run_id is not None:\n        event_metadata["run_id"] = run_id\n    if attempt is not None:\n        event_metadata["attempt"] = attempt\n\n    with sqlite3.connect(DB_FILE) as db:\n        db.execute(\n            \'\'\'INSERT INTO checkpoints(task_id, phase, state, created_at)\n               VALUES (?, ?, ?, ?)\'\'\',\n            (\n                effective_task_id,\n                f\'mutation:{transaction_id}:{phase}\',\n                state,\n                now_iso(),\n            ),\n        )\n        db.execute(\n            \'\'\'INSERT INTO agent_events\n               (task_id, goal_id, parent_task_id, run_id, attempt,\n                event_type, message, metadata, created_at)\n               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\'\'\',\n            (\n                effective_task_id,\n                goal_id,\n                parent_task_id,\n                run_id,\n                attempt,\n                \'MUTATION_CHECKPOINT\',\n                f\'{transaction_id}: {state}\',\n                json.dumps(event_metadata, sort_keys=True),\n                now_iso(),\n            ),\n        )\n        db.commit()\n\n\n', "mutation checkpoint_event")
    text = replace_exact(text, '        db.execute(\n            \'\'\'INSERT INTO agent_events(event_type, message, metadata, created_at)\n               VALUES (?, ?, ?, ?)\'\'\',\n            (\'MUTATION_TRANSACTION\',\n             f"{tx[\'transaction_id\']}: {tx[\'status\']}",\n             json.dumps({\n                 \'transaction_id\': tx[\'transaction_id\'],\n                 \'project\': tx[\'project\'],\n                 \'files\': [f[\'path\'] for f in tx[\'files\']],\n                 \'status\': tx[\'status\'],\n             }, sort_keys=True), now_iso()),\n        )\n', '        effective_task_id, goal_id, parent_task_id, run_id, attempt = (\n            execution_context(tx.get(\'task_id\'))\n        )\n        event_metadata = {\n            \'transaction_id\': tx[\'transaction_id\'],\n            \'project\': tx[\'project\'],\n            \'files\': [f[\'path\'] for f in tx[\'files\']],\n            \'status\': tx[\'status\'],\n        }\n        if effective_task_id is not None:\n            event_metadata[\'task_id\'] = effective_task_id\n        if run_id is not None:\n            event_metadata[\'run_id\'] = run_id\n        if attempt is not None:\n            event_metadata[\'attempt\'] = attempt\n\n        db.execute(\n            \'\'\'INSERT INTO agent_events\n               (task_id, goal_id, parent_task_id, run_id, attempt,\n                event_type, message, metadata, created_at)\n               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\'\'\',\n            (\n                effective_task_id,\n                goal_id,\n                parent_task_id,\n                run_id,\n                attempt,\n                \'MUTATION_TRANSACTION\',\n                f"{tx[\'transaction_id\']}: {tx[\'status\']}",\n                json.dumps(event_metadata, sort_keys=True),\n                now_iso(),\n            ),\n        )\n', "mutation transaction event")
    out = backup(MUTATION)
    MUTATION.write_text(text, encoding="utf-8")
    validate(MUTATION)
    return out

def main():
    if not GATEWAY.is_file():
        raise SystemExit(f"Missing gateway: {GATEWAY}")
    if not MUTATION.is_file():
        raise SystemExit(f"Missing mutation engine: {MUTATION}")
    gateway_backup = patch_gateway()
    mutation_backup = patch_mutation()
    print("PATCH_OK")
    print(f"gateway_backup={gateway_backup}")
    print(f"mutation_backup={mutation_backup}")

if __name__ == "__main__":
    main()
