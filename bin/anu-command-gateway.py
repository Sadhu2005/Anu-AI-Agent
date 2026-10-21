#!/usr/bin/env python3

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))

CONFIG_FILE = BASE / "config" / "anu-command-gateway.yaml"
PROFILE_FILE = BASE / "config" / "anu-execution-profiles.yaml"
DB_FILE = BASE / "brain" / "anu-brain.db"
LOG_FILE = BASE / "logs" / "anu-actions.jsonl"
GOVERNOR = BASE / "bin" / "anu-resource-governor.py"
SANDBOX_CLIENT = BASE / "bin" / "anu-sandbox-client.py"
NORMALIZER = BASE / "bin" / "anu-result-normalizer.py"
STATE_FILE = BASE / "runtime" / "anu-state.json"


def host_command_env():
    """Minimal explicit environment for host commands (review F4)."""
    home = BASE / "home"
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def validate_git_push_argv(argv):
    """V0.9.5: allow-list git push only to agent/*; deny force and default branch."""
    if not argv:
        return list(argv or [])
    parts = [str(a) for a in argv]
    if "push" not in parts:
        return parts
    if any(a in ("--force", "-f", "--force-with-lease") for a in parts):
        raise PermissionError("git push --force is denied by command gateway")
    # Find refspecs after remote name (best-effort).
    try:
        push_i = parts.index("push")
    except ValueError:
        return parts
    refspecs = [
        a for a in parts[push_i + 1 :]
        if a and not a.startswith("-") and a not in ("origin", "upstream")
        and (":" in a or a.startswith("agent/") or a.startswith("refs/heads/agent/"))
    ]
    if not refspecs:
        # bare `git push` while on agent/* is OK at workflow layer; gateway requires explicit agent ref
        raise PermissionError("git push requires explicit agent/* refspec")
    for spec in refspecs:
        dest = spec.split(":", 1)[-1].replace("refs/heads/", "")
        if not dest.startswith("agent/"):
            raise PermissionError(
                f"git push destination must be agent/*, got {dest!r}"
            )
        if dest in ("main", "master"):
            raise PermissionError("git push to default branch denied")
    return parts


def harden_host_argv(command_name, argv, project_path=None):
    """Inject safe git -c flags for repository-controlled config (F4).

    V0.8.11: also set ``safe.directory`` so host git works when the discovered
    repo (often ``ANU_BASE`` for workspace projects without their own ``.git``)
    is owned by a different uid than the agent (e.g. root-owned ``.git``).
    """
    if not argv:
        return list(argv)
    if command_name in ("git_push", "git-push") or (
        argv and Path(argv[0]).name == "git" and "push" in [str(a) for a in argv]
    ):
        validate_git_push_argv(argv)
    exe = Path(argv[0]).name
    if exe != "git":
        return list(argv)
    hardened = [
        argv[0],
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "safe.directory=*",
    ]
    # Prefer explicit dirs when known (multi -c safe.directory is additive).
    for directory in (project_path, BASE):
        if directory is None:
            continue
        try:
            resolved = str(Path(directory).resolve())
        except Exception:
            continue
        hardened.extend(["-c", f"safe.directory={resolved}"])
    rest = list(argv[1:])
    if command_name in ("git_diff", "git_diff_stat") or (
        rest and rest[0] == "diff"
    ):
        # Insert after subcommand if present.
        if rest and rest[0] == "diff":
            rest = ["diff", "--no-ext-diff", "--no-textconv", *rest[1:]]
        else:
            rest = ["--no-ext-diff", "--no-textconv", *rest]
    return hardened + rest


def now():
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_jsonl(record):
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")



def execution_context():
    """Return optional task execution context from the inherited environment."""
    task_id = None
    run_id = None
    attempt = None

    raw_task_id = os.environ.get("ANU_TASK_ID")
    raw_run_id = os.environ.get("ANU_RUN_ID")
    raw_attempt = os.environ.get("ANU_ATTEMPT")

    if raw_task_id:
        try:
            task_id = int(raw_task_id)
        except ValueError:
            task_id = None

    if raw_run_id:
        run_id = raw_run_id

    if raw_attempt:
        try:
            attempt = int(raw_attempt)
        except ValueError:
            attempt = None

    return task_id, run_id, attempt


def write_event(event_type, message, metadata=None):
    timestamp = now()

    raw_task_id = os.environ.get("ANU_TASK_ID", "").strip()
    raw_attempt = os.environ.get("ANU_ATTEMPT", "").strip()
    task_id = int(raw_task_id) if raw_task_id.isdigit() else None
    attempt = int(raw_attempt) if raw_attempt.isdigit() else None
    run_id = os.environ.get("ANU_RUN_ID", "").strip() or None

    event_metadata = dict(metadata or {})
    if task_id is not None:
        event_metadata["task_id"] = task_id
    if run_id is not None:
        event_metadata["run_id"] = run_id
    if attempt is not None:
        event_metadata["attempt"] = attempt

    write_jsonl({
        "timestamp": timestamp,
        "agent": "ANU",
        "event_type": event_type,
        "message": message,
        "metadata": event_metadata,
    })

    try:
        import importlib.util as _ilu
        _db_mod = None
        for _cand in (
            BASE / "bin" / "anu-db-v0.1.py",
            Path(__file__).resolve().parent / "anu-db-v0.1.py",
        ):
            if _cand.is_file():
                _spec = _ilu.spec_from_file_location(
                    "anu_db_v0_1_gateway",
                    _cand,
                )
                _db_mod = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_db_mod)
                break
        if _db_mod is None:
            raise RuntimeError("anu-db-v0.1.py not found")
        with _db_mod.db_connect(DB_FILE) as db:
            goal_id = None
            parent_task_id = None
            if task_id is not None:
                row = db.execute(
                    "SELECT goal_id, parent_task_id FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if row:
                    goal_id, parent_task_id = row[0], row[1]

            db.execute(
                """
                INSERT INTO agent_events
                (task_id, goal_id, parent_task_id, run_id, attempt,
                 event_type, message, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    goal_id,
                    parent_task_id,
                    run_id,
                    attempt,
                    event_type,
                    message,
                    json.dumps(event_metadata, sort_keys=True),
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            db.commit()
    except Exception as exc:
        try:
            import importlib.util as _ilu2
            for _cand2 in (
                BASE / "bin" / "anu-db-v0.1.py",
                Path(__file__).resolve().parent / "anu-db-v0.1.py",
            ):
                if _cand2.is_file():
                    _spec2 = _ilu2.spec_from_file_location(
                        "anu_db_v0_1_gateway_drop",
                        _cand2,
                    )
                    _mod2 = _ilu2.module_from_spec(_spec2)
                    _spec2.loader.exec_module(_mod2)
                    _mod2.note_dropped_event(
                        exc, where="command_gateway.write_event"
                    )
                    break
        except Exception:
            print(
                f"ANU_DROPPED_EVENT where=command_gateway.write_event: {exc}",
                file=sys.stderr,
                flush=True,
            )

def resolve_project(workspace, project):
    if project is None:
        raise ValueError("Project is required.")

    if os.path.isabs(project):
        raise ValueError(
            "Absolute project paths are not allowed."
        )

    if ".." in Path(project).parts:
        raise ValueError(
            "Path traversal is not allowed."
        )

    workspace = workspace.resolve()
    target = (workspace / project).resolve()

    try:
        target.relative_to(workspace)
    except ValueError:
        raise ValueError(
            "Project is outside ANU workspace."
        )

    if not target.exists():
        raise ValueError(
            f"Project does not exist: {project}"
        )

    if not target.is_dir():
        raise ValueError(
            "Project must be a directory."
        )

    return target


def resolve_profile(command_name):
    config = load_yaml(PROFILE_FILE)
    root = config.get("execution_profiles", {})

    if not root.get("enabled", False):
        raise RuntimeError(
            "Execution profiles are disabled."
        )

    command_profiles = root.get(
        "command_profiles",
        {}
    )

    profiles = root.get(
        "profiles",
        {}
    )

    profile_name = command_profiles.get(
        command_name
    )

    if not profile_name:
        raise ValueError(
            f"No execution profile configured for command: "
            f"{command_name}"
        )

    profile = profiles.get(profile_name)

    if not profile:
        raise ValueError(
            f"Unknown execution profile: {profile_name}"
        )

    return profile_name, profile


def resource_admission(profile_name, profile):
    ram_mb = float(profile["ram_mb"])
    storage_mb = float(profile["storage_mb"])

    result = subprocess.run(
        [
            str(GOVERNOR),
            "task",
            "--ram-mb",
            str(ram_mb),
            "--size-mb",
            str(storage_mb),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        shell=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Resource Governor failed: "
            + (result.stderr.strip() or "unknown error")
        )

    try:
        decision = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid Resource Governor response: {exc}"
        )

    decision_name = decision.get(
        "decision",
        "DENY"
    )

    write_event(
        "RESOURCE_ADMISSION",
        f"{profile_name}: {decision_name}",
        {
            "profile": profile_name,
            "requested_ram_mb": ram_mb,
            "requested_storage_mb": storage_mb,
            "decision": decision,
        },
    )

    return decision


def normalize_sandbox_result(record):
    """
    Convert a raw sandbox execution record into ANU's
    normalized result taxonomy.
    """

    process = subprocess.run(
        [
            sys.executable,
            str(NORMALIZER),
        ],
        input=json.dumps(record),
        capture_output=True,
        text=True,
        timeout=5,
        shell=False,
    )

    if process.returncode != 0:
        raise RuntimeError(
            "Result Normalizer failed: "
            + (
                process.stderr.strip()
                or "unknown normalizer error"
            )
        )

    try:
        normalized = json.loads(
            process.stdout.strip()
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid Result Normalizer response: {exc}"
        )

    if not isinstance(normalized, dict):
        raise RuntimeError(
            "Result Normalizer response is not an object."
        )

    return normalized


def persist_test_result(operation, normalized, task_id=None):
    """
    Persist sandbox test/compile results.

    task_id is supplied by the Task Engine when the execution belongs to a task.
    """

    if operation not in {
        "python_test",
        "python_compile",
    }:
        return False

    status = normalized.get(
        "status",
        "RUNTIME_ERROR",
    )

    output = normalized.get(
        "output",
        "",
    )

    with sqlite3.connect(DB_FILE) as db:
        db.execute(
            """
            INSERT INTO test_results
                (task_id, test_name, status, output)
            VALUES (?, ?, ?, ?)
            """,
            (
                task_id,
                operation,
                status,
                output,
            ),
        )
        db.commit()

    return True


def finalize_sandbox_result(
    record,
    command_name,
    profile_name,
    operation,
    project_path,
    client_argv,
    task_id=None,
    timeout_return_code=None,
):
    """
    Normalize, persist, log, print, and return a sandbox result.
    """

    raw_ok = bool(
        record.get("ok", False)
    )

    record["raw_ok"] = raw_ok

    normalized = None
    normalization_error = None
    persisted = False

    try:
        normalized = normalize_sandbox_result(
            record
        )

        record["normalized_result"] = normalized

        record["ok"] = bool(
            normalized.get(
                "success",
                False,
            )
        )

        try:
            persisted = persist_test_result(
                operation,
                normalized,
                task_id=task_id,
            )

        except Exception as exc:
            normalization_error = (
                f"Test result persistence failed: {exc}"
            )

            write_event(
                "TEST_RESULT_PERSIST_FAILED",
                f"{command_name}: test result persistence failed",
                {
                    "command": command_name,
                    "profile": profile_name,
                    "operation": operation,
                    "project": str(project_path),
                    "error": str(exc),
                    "status": normalized.get(
                        "status",
                        "UNKNOWN",
                    ),
                },
            )

    except Exception as exc:
        normalization_error = str(exc)

        record["normalized_result"] = None

        # Preserve raw transport result if normalization itself fails.
        record["ok"] = raw_ok

        write_event(
            "RESULT_NORMALIZATION_FAILED",
            f"{command_name}: result normalization failed",
            {
                "command": command_name,
                "profile": profile_name,
                "operation": operation,
                "project": str(project_path),
                "error": normalization_error,
            },
        )

    record["test_result_persisted"] = persisted

    if normalization_error:
        record["normalization_error"] = normalization_error

    normalized_status = (
        normalized.get("status")
        if normalized
        else (
            "SUCCESS"
            if raw_ok
            else "RUNTIME_ERROR"
        )
    )

    write_jsonl(
        {
            "timestamp": record["timestamp"],
            "agent": "ANU",
            "operation": "sandbox_command",
            "command_name": command_name,
            "profile": profile_name,
            "runner": "sandbox",
            "sandbox_operation": operation,
            "status": normalized_status,
            "success": bool(record["ok"]),
            "raw_ok": raw_ok,
            "project": str(project_path),
            "argv": client_argv,
            "exit_code": record.get(
                "exit_code"
            ),
            "client_exit_code": record.get(
                "client_exit_code"
            ),
            "output": record.get(
                "output",
                "",
            ),
            "error": record.get(
                "error",
                "",
            ),
        }
    )

    write_event(
        "COMMAND_FINISHED",
        f"{command_name}: sandbox {normalized_status}",
        {
            "command": command_name,
            "profile": profile_name,
            "runner": "sandbox",
            "operation": operation,
            "project": str(project_path),
            "exit_code": record.get(
                "exit_code"
            ),
            "client_exit_code": record.get(
                "client_exit_code"
            ),
            "status": normalized_status,
            "success": bool(record["ok"]),
            "result_persisted": persisted,
        },
    )

    print(
        json.dumps(
            record,
            indent=2,
        )
    )

    if timeout_return_code is not None:
        return timeout_return_code

    return 0 if record["ok"] else 1


def execute_sandbox_command(
    command_name,
    definition,
    profile_name,
    profile,
    project,
    project_path,
    decision,
    task_id=None,
):
    if not profile.get("sandbox_required", False):
        raise RuntimeError(
            f"Command {command_name} is marked as sandboxed "
            "but its execution profile does not require sandboxing."
        )

    if definition.get("type") != "sandbox":
        raise RuntimeError(
            f"Sandbox profile mismatch for command: {command_name}"
        )

    operation = definition.get("operation")

    if operation not in {
        "python_test",
        "python_compile",
    }:
        raise ValueError(
            f"Sandbox operation is not allowlisted: {operation}"
        )

    client_argv = [
        str(SANDBOX_CLIENT),
        "--operation",
        operation,
        "--project",
        project,
    ]

    timeout_seconds = int(
        profile["timeout_seconds"]
    )

    max_output_bytes = int(
        profile["max_output_bytes"]
    )

    write_event(
        "COMMAND_STARTED",
        f"{command_name}: sandbox execution started",
        {
            "command": command_name,
            "profile": profile_name,
            "runner": "sandbox",
            "operation": operation,
            "project": str(project_path),
            "argv": client_argv,
        },
    )

    try:
        process = subprocess.run(
            client_argv,
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )

    except subprocess.TimeoutExpired:
        record = {
            "ok": False,
            "agent": "ANU",
            "command": client_argv,
            "project": str(project_path),
            "profile": {
                "name": profile_name,
                "ram_mb": profile["ram_mb"],
                "storage_mb": profile["storage_mb"],
                "timeout_seconds": timeout_seconds,
                "max_output_bytes": max_output_bytes,
                "network": profile["network"],
                "filesystem_mode": profile[
                    "filesystem_mode"
                ],
                "sandbox_required": True,
            },
            "runner": "sandbox",
            "sandbox_operation": operation,
            "exit_code": None,
            "client_exit_code": None,
            "output": "",
            "error": "Sandbox client timed out.",
            "output_truncated": False,
            "resource_admission": decision,
            "sandbox_resource_admission": None,
            "sandbox": {},
            "timestamp": now(),
        }

        return finalize_sandbox_result(
            record,
            command_name,
            profile_name,
            operation,
            project_path,
            client_argv,
            task_id=task_id,
            timeout_return_code=124,
        )

    raw_stdout = process.stdout or ""
    raw_stderr = process.stderr or ""

    inner = None

    try:
        parsed = json.loads(
            raw_stdout.strip()
        )

        if isinstance(parsed, dict):
            inner = parsed

    except json.JSONDecodeError:
        inner = None

    if inner is not None:
        ok = bool(
            inner.get(
                "ok",
                process.returncode == 0,
            )
        )

        exit_code = inner.get(
            "exit_code",
            process.returncode,
        )

        output = inner.get(
            "output",
            "",
        )

        error = inner.get(
            "error",
            "",
        )

        sandbox_resource_admission = inner.get(
            "resource_admission"
        )

        sandbox_details = inner.get(
            "sandbox",
            {},
        )

        output_truncated = bool(
            inner.get(
                "output_truncated",
                False,
            )
        )

    else:
        ok = process.returncode == 0

        exit_code = process.returncode

        output = raw_stdout

        error = raw_stderr

        sandbox_resource_admission = None

        sandbox_details = {}

        output_truncated = False

    if not isinstance(output, str):
        output = str(output)

    raw_output = output.encode(
        "utf-8"
    )

    if len(raw_output) > max_output_bytes:
        output = raw_output[
            :max_output_bytes
        ].decode(
            "utf-8",
            errors="replace",
        )

        output_truncated = True

    if raw_stderr:
        error = (
            (error + "\n" if error else "")
            + raw_stderr
        ).strip()

    record = {
        "ok": ok,
        "agent": "ANU",
        "command": client_argv,
        "project": str(project_path),
        "profile": {
            "name": profile_name,
            "ram_mb": profile["ram_mb"],
            "storage_mb": profile["storage_mb"],
            "timeout_seconds": timeout_seconds,
            "max_output_bytes": max_output_bytes,
            "network": profile["network"],
            "filesystem_mode": profile[
                "filesystem_mode"
            ],
            "sandbox_required": True,
        },
        "runner": "sandbox",
        "sandbox_operation": operation,
        "exit_code": exit_code,
        "client_exit_code": process.returncode,
        "output": output,
        "error": error,
        "output_truncated": output_truncated,
        "resource_admission": decision,
        "sandbox_resource_admission": (
            sandbox_resource_admission
        ),
        "sandbox": sandbox_details,
        "timestamp": now(),
    }

    return finalize_sandbox_result(
        record,
        command_name,
        profile_name,
        operation,
        project_path,
        client_argv,
        task_id=task_id,
    )


COMMANDS = {
    "pwd": {
        "executable": "/usr/bin/pwd",
        "argv": [],
    },
    "git_version": {
        "executable": "/usr/bin/git",
        "argv": ["--version"],
    },
    "git_status": {
        "executable": "/usr/bin/git",
        "argv": ["status"],
    },
    "git_status_short": {
        "executable": "/usr/bin/git",
        "argv": ["status", "--short"],
    },
    "git_branch": {
        "executable": "/usr/bin/git",
        "argv": ["branch"],
    },
    "git_branch_current": {
        "executable": "/usr/bin/git",
        "argv": ["branch", "--show-current"],
    },
    "git_diff": {
        "executable": "/usr/bin/git",
        "argv": ["diff"],
    },
    "git_diff_stat": {
        "executable": "/usr/bin/git",
        "argv": ["diff", "--stat"],
    },
    "python_version": {
        "executable": "/usr/bin/python3",
        "argv": ["--version"],
    },
    "node_version": {
        "executable": "/usr/bin/node",
        "argv": ["--version"],
    },
    "npm_version": {
        "executable": "/usr/bin/npm",
        "argv": ["--version"],
    },
    "python_test": {
        "type": "sandbox",
        "operation": "python_test",
    },
    "python_compile": {
        "type": "sandbox",
        "operation": "python_compile",
    },
}


def assert_allowlist_aligned(config):
    """Fail closed when YAML allowed_commands drifts from COMMANDS (F13)."""
    allowed = config.get("allowed_commands") or {}
    if not isinstance(allowed, dict):
        raise RuntimeError("gateway allowed_commands must be a mapping")
    yaml_names = set(allowed.keys())
    code_names = set(COMMANDS.keys())
    if yaml_names != code_names:
        raise RuntimeError(
            "allow-list drift between YAML and COMMANDS: "
            f"only_in_yaml={sorted(yaml_names - code_names)} "
            f"only_in_code={sorted(code_names - yaml_names)}"
        )


def cmd_status(config):
    gateway = config.get("gateway", {})
    resource = config.get("resource_control", {})

    profiles = load_yaml(PROFILE_FILE)
    profile_root = profiles.get(
        "execution_profiles",
        {}
    )

    print(
        json.dumps(
            {
                "ok": True,
                "agent": "ANU",
                "gateway": gateway,
                "workspace": config["workspace"]["root"],
                "resource_control": resource,
                "execution_profiles": {
                    "enabled": profile_root.get(
                        "enabled", False
                    ),
                    "version": profile_root.get(
                        "version"
                    ),
                    "profiles": list(
                        profile_root.get(
                            "profiles",
                            {}
                        ).keys()
                    ),
                    "command_profiles": (
                        profile_root.get(
                            "command_profiles",
                            {}
                        )
                    ),
                },
                "commands": sorted(COMMANDS),
                "timestamp": now(),
            },
            indent=2,
        )
    )


def _record_approval_required(command_name, project, task_id, decision):
    """Best-effort V0.6.7 approval record when Governor returns APPROVAL."""
    payload = {"approval_id": None, "recorded": False}
    try:
        import importlib.util
        approval_path = BASE / "bin" / "anu-approval-service-v0.1.py"
        if not approval_path.is_file():
            approval_path = Path(__file__).resolve().parent / "anu-approval-service-v0.1.py"
        if not approval_path.is_file():
            return payload
        spec = importlib.util.spec_from_file_location(
            "anu_approval_service_v0_1_gateway",
            approval_path,
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        db = mod.db_connect(DB_FILE)
        try:
            mod.migrate(db)
            goal_id = None
            if task_id is not None:
                try:
                    row = db.execute(
                        "SELECT goal_id FROM tasks WHERE id = ?",
                        (task_id,),
                    ).fetchone()
                    if row:
                        goal_id = row[0]
                except Exception:
                    goal_id = None
            size_mb = None
            if isinstance(decision, dict):
                size_mb = decision.get("requested_size_mb")
            record = mod.request_approval(
                db,
                action=command_name,
                task_id=task_id,
                goal_id=goal_id,
                reason=(
                    decision.get("reason")
                    if isinstance(decision, dict)
                    else "governor_approval"
                ),
                risk="medium",
                proposed_change=json.dumps({
                    "command": command_name,
                    "project": project,
                    "size_mb": size_mb,
                }, sort_keys=True),
                put_task_waiting=task_id is not None,
            )
            payload["approval_id"] = record.get("id")
            payload["recorded"] = True
            write_event(
                "APPROVAL_REQUIRED",
                f"{command_name}: approval {record.get('id')}",
                {
                    "project": project,
                    "task_id": task_id,
                    "approval_id": record.get("id"),
                },
            )
        finally:
            db.close()
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    return payload


def cmd_run(config, command_name, project, task_id=None):
    gateway = config.get("gateway", {})
    if not gateway.get("enabled", False):
        raise RuntimeError(
            "Command Gateway is disabled."
        )

    if command_name not in COMMANDS:
        write_event(
            "COMMAND_DENIED",
            "Unknown command",
            {
                "command": command_name,
                "project": project,
            },
        )
        raise ValueError(
            f"Command not allowed: {command_name}"
        )

    workspace = Path(
        config["workspace"]["root"]
    ).resolve()

    project_path = resolve_project(
        workspace,
        project
    )

    profile_name, profile = resolve_profile(
        command_name
    )

    decision = resource_admission(
        profile_name,
        profile
    )

    decision_name = decision.get(
        "decision"
    )

    if decision_name == "DENY":
        raise PermissionError(
            f"Resource admission denied: "
            f"{decision.get('reasons', [])}"
        )

    if decision_name == "PAUSE":
        raise RuntimeError(
            "Execution paused by Resource Governor."
        )

    if decision_name == "APPROVAL":
        # V0.6.7: record approval request and park task when task_id known.
        approval_payload = _record_approval_required(
            command_name=command_name,
            project=project,
            task_id=task_id,
            decision=decision,
        )
        raise PermissionError(
            "Execution requires approval."
            + (
                f" approval_id={approval_payload.get('approval_id')}"
                if approval_payload.get("approval_id")
                else ""
            )
        )

    if decision_name == "PREFLIGHT":
        write_event(
            "PREFLIGHT_REQUIRED",
            f"{command_name}: PREFLIGHT",
            {
                "project": str(project_path),
                "profile": profile_name,
            },
        )

    definition = COMMANDS[command_name]

    command_is_sandboxed = (
        definition.get("type") == "sandbox"
    )

    profile_requires_sandbox = bool(
        profile.get("sandbox_required", False)
    )

    if command_is_sandboxed != profile_requires_sandbox:
        raise RuntimeError(
            "Execution profile and command sandbox policy mismatch."
        )

    if command_is_sandboxed:
        return execute_sandbox_command(
            command_name,
            definition,
            profile_name,
            profile,
            project,
            project_path,
            decision,
            task_id=task_id,
        )

    argv = harden_host_argv(
        command_name,
        [
            definition["executable"],
            *definition["argv"],
        ],
        project_path=project_path,
    )

    timeout_seconds = int(
        profile["timeout_seconds"]
    )

    max_output_bytes = int(
        profile["max_output_bytes"]
    )

    write_event(
        "COMMAND_STARTED",
        f"{command_name}: execution started",
        {
            "command": command_name,
            "profile": profile_name,
            "project": str(project_path),
            "argv": argv,
        },
    )

    process = subprocess.run(
        argv,
        cwd=str(project_path),
        capture_output=True,
        timeout=timeout_seconds,
        shell=False,
        env=host_command_env(),
    )

    stdout = process.stdout.decode(
        "utf-8",
        errors="replace"
    )

    stderr = process.stderr.decode(
        "utf-8",
        errors="replace"
    )

    combined = stdout
    if stderr:
        combined += stderr

    truncated = False

    if len(combined.encode("utf-8")) > max_output_bytes:
        raw = combined.encode("utf-8")
        combined = raw[:max_output_bytes].decode(
            "utf-8",
            errors="replace"
        )
        truncated = True

    record = {
        "ok": process.returncode == 0,
        "agent": "ANU",
        "command": argv,
        "project": str(project_path),
        "profile": {
            "name": profile_name,
            "ram_mb": profile["ram_mb"],
            "storage_mb": profile["storage_mb"],
            "timeout_seconds": timeout_seconds,
            "max_output_bytes": max_output_bytes,
            "network": profile["network"],
            "filesystem_mode": profile[
                "filesystem_mode"
            ],
            "sandbox_required": profile[
                "sandbox_required"
            ],
        },
        "exit_code": process.returncode,
        "output": combined,
        "output_truncated": truncated,
        "resource_admission": decision,
        "timestamp": now(),
    }

    write_jsonl({
        "timestamp": record["timestamp"],
        "agent": "ANU",
        "operation": "command",
        "command_name": command_name,
        "profile": profile_name,
        "status": (
            "success"
            if process.returncode == 0
            else "failed"
        ),
        "project": str(project_path),
        "argv": argv,
        "output": combined,
        "error": stderr,
    })

    write_event(
        "COMMAND_FINISHED",
        f"{command_name}: "
        + (
            "success"
            if process.returncode == 0
            else "failed"
        ),
        {
            "command": command_name,
            "profile": profile_name,
            "project": str(project_path),
            "exit_code": process.returncode,
        },
    )

    print(
        json.dumps(
            record,
            indent=2
        )
    )

    return process.returncode


def main():
    parser = argparse.ArgumentParser(
        description="ANU Command Gateway"
    )

    sub = parser.add_subparsers(
        dest="action",
        required=True
    )

    sub.add_parser("status")

    run_parser = sub.add_parser("run")

    run_parser.add_argument(
        "command",
        choices=sorted(COMMANDS)
    )

    run_parser.add_argument(
        "--project",
        required=True
    )

    run_parser.add_argument(
        "--task-id",
        type=int,
        default=None,
        help="ANU Task Engine task ID for result attribution",
    )

    args = parser.parse_args()

    config = load_yaml(CONFIG_FILE)
    assert_allowlist_aligned(config)

    if args.action == "status":
        cmd_status(config)
        return 0

    if args.action == "run":
        return cmd_run(
            config,
            args.command,
            args.project,
            task_id=args.task_id,
        )

    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.TimeoutExpired:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Command timed out."
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        sys.exit(124)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc)
                },
                indent=2,
            )
        )
        sys.exit(1)
