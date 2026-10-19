#!/usr/bin/env python3
"""ANU Capability Registry v0.1 (V0.6.2 step 1).

Turns the existing Command Gateway allow-list and execution profiles into
explicit **capabilities**. Unknown names fail closed.

This module does not execute tools. It answers:

- Which capabilities exist?
- What profile and contracts apply?
- Is this name permitted?
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


BASE = Path(os.environ.get("ANU_BASE", "/srv/anu"))
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent

GATEWAY_CONFIG = BASE / "config" / "anu-command-gateway.yaml"
PROFILE_CONFIG = BASE / "config" / "anu-execution-profiles.yaml"
DB_FILE = BASE / "brain" / "anu-brain.db"
GATEWAY_BIN = BASE / "bin" / "anu-command-gateway.py"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def audit_capability_event(
    db,
    event_type,
    message,
    metadata=None,
    *,
    task_id=None,
    goal_id=None,
):
    """Write a capability audit row to agent_events (best-effort)."""
    if db is None or not hasattr(db, "execute"):
        raise TypeError("db must provide an execute() method")
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("event_type must be a non-empty string")
    if not isinstance(message, str):
        raise TypeError("message must be a string")

    metadata = dict(metadata or {})
    if task_id is not None:
        if isinstance(task_id, bool) or not isinstance(task_id, int):
            raise TypeError("task_id must be an integer or None")
        metadata.setdefault("task_id", task_id)
    if goal_id is not None:
        if isinstance(goal_id, bool) or not isinstance(goal_id, int):
            raise TypeError("goal_id must be an integer or None")
        metadata.setdefault("goal_id", goal_id)

    try:
        db.execute(
            """INSERT INTO agent_events
                   (task_id, goal_id, parent_task_id, run_id, attempt,
                    event_type, message, metadata, created_at)
               VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?, ?)""",
            (
                task_id,
                goal_id,
                event_type.strip(),
                message,
                json.dumps(metadata, sort_keys=True),
                now_iso(),
            ),
        )
        db.commit()
        return True
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        try:
            for candidate in (
                BASE / "bin" / "anu-db-v0.1.py",
                _HERE / "anu-db-v0.1.py",
            ):
                if candidate.is_file():
                    spec = importlib.util.spec_from_file_location(
                        "anu_db_v0_1_cap_audit",
                        candidate,
                    )
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    mod.note_dropped_event(
                        exc, where="capability_registry.audit"
                    )
                    break
        except Exception:
            print(
                f"ANU_DROPPED_EVENT where=capability_registry.audit: {exc}",
                file=sys.stderr,
                flush=True,
            )
        return False

# Friendly capability names from the V0.6.2 design map onto gateway commands.
# Only aliases that resolve to a registered command are exposed.
CAPABILITY_ALIASES = {
    "python.test": "python_test",
    "python.compile": "python_compile",
    "git.status": "git_status",
    "git.diff": "git_diff",
}

# Sandbox commands live in the gateway Python allow-list (COMMANDS), not
# always in anu-command-gateway.yaml. Register them when profiled.
SANDBOX_COMMAND_SPECS = {
    "python_test": {
        "type": "sandbox",
        "operation": "python_test",
        "executable": None,
        "argv": [],
    },
    "python_compile": {
        "type": "sandbox",
        "operation": "python_compile",
        "executable": None,
        "argv": [],
    },
}

# V0.8.5: research runs via anu-research (host HTTPS allow-list), not gateway.
RESEARCH_COMMAND_SPECS = {
    "research": {
        "type": "research",
        "operation": "research",
        "executable": None,
        "argv": [],
    },
}

# browser / filesystem.write remain hard-disabled. research is registered but
# approval-gated at evaluate/invoke (V0.6.7 boundary + V0.8.5 enablement).
DISABLED_CAPABILITIES = frozenset({
    "browser",
    "filesystem.write",
})

APPROVAL_GATED_CAPABILITIES = frozenset({
    "research",
})


def _load_approval_module():
    for candidate in (
        BASE / "bin" / "anu-approval-service-v0.1.py",
        _HERE / "anu-approval-service-v0.1.py",
        _REPO / "bin" / "anu-approval-service-v0.1.py",
    ):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "anu_approval_service_v0_1_cap",
                candidate,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise FileNotFoundError("anu-approval-service-v0.1.py not found")


def _load_valid_approval(db, task_id, action):
    """Return approved approval row or None (fail closed)."""
    try:
        mod = _load_approval_module()
        return mod.has_valid_approval(db, task_id, action)
    except Exception:
        return None


def _research_bin():
    for candidate in (
        BASE / "bin" / "anu-research-v0.1.py",
        _HERE / "anu-research-v0.1.py",
        _REPO / "bin" / "anu-research-v0.1.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def _default_gateway_path():
    live = GATEWAY_CONFIG
    if live.is_file():
        return live
    return _REPO / "config" / "anu-command-gateway.yaml"


def _default_profile_path():
    live = PROFILE_CONFIG
    if live.is_file():
        return live
    return _REPO / "config" / "anu-execution-profiles.yaml"


def load_yaml(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    return data


def _failure_contract(command_name, profile):
    """Typed failures that feed V0.6.0 classification later."""
    classes = ["contract", "environment", "resource"]
    if profile.get("sandbox_required"):
        classes.append("integrity")
    return {
        "classes": classes,
        "notes": (
            f"gateway command {command_name!r} fails closed on deny, "
            "timeout, and invalid output"
        ),
    }


def _input_contract(command_name, command_spec):
    return {
        "type": "object",
        "required": ["project"],
        "properties": {
            "project": {
                "type": "string",
                "description": "Project name under workspace root",
            },
            "task_id": {
                "type": ["integer", "null"],
                "description": "Optional Task Engine attribution",
            },
        },
        "command": command_name,
        "argv_fixed": list(command_spec.get("argv") or []),
        "executable": command_spec.get("executable"),
    }


def _output_contract(command_name, profile):
    return {
        "type": "object",
        "required": ["ok", "command", "stdout", "stderr", "exit_code"],
        "properties": {
            "ok": {"type": "boolean"},
            "command": {"type": "string", "const": command_name},
            "stdout": {"type": "string"},
            "stderr": {"type": "string"},
            "exit_code": {"type": "integer"},
        },
        "max_output_bytes": profile.get("max_output_bytes"),
    }


def _permission_contract(command_name, profile):
    return {
        "invoker": "agent",
        "projects": "workspace",
        "network": bool(profile.get("network", False)),
        "filesystem_mode": profile.get("filesystem_mode", "workspace-read"),
        "sandbox_required": bool(profile.get("sandbox_required", False)),
        "gateway_command": command_name,
    }


def build_capability(name, command_name, command_spec, profile_name, profile):
    return {
        "name": name,
        "command": command_name,
        "enabled": True,
        "permission": _permission_contract(command_name, profile),
        "resource_limit": {
            "profile": profile_name,
            "ram_mb": profile.get("ram_mb"),
            "storage_mb": profile.get("storage_mb"),
            "timeout_seconds": profile.get("timeout_seconds"),
            "max_output_bytes": profile.get("max_output_bytes"),
        },
        "input_contract": _input_contract(command_name, command_spec),
        "output_contract": _output_contract(command_name, profile),
        "failure_contract": _failure_contract(command_name, profile),
        "audit_record": {
            "events": ["CAPABILITY_REQUEST", "CAPABILITY_RESULT"],
            "fields": [
                "capability",
                "command",
                "project",
                "task_id",
                "profile",
                "ok",
                "exit_code",
            ],
        },
    }


class CapabilityRegistry:
    """In-memory registry built from gateway + profile config."""

    def __init__(self, gateway_config, profile_config):
        if not isinstance(gateway_config, dict):
            raise TypeError("gateway_config must be a dict")
        if not isinstance(profile_config, dict):
            raise TypeError("profile_config must be a dict")

        self._gateway = gateway_config
        self._profiles_root = profile_config.get("execution_profiles") or {}
        self._by_name = {}
        self._load()

    @classmethod
    def from_files(cls, gateway_path=None, profile_path=None):
        gateway_path = Path(gateway_path) if gateway_path else _default_gateway_path()
        profile_path = Path(profile_path) if profile_path else _default_profile_path()
        return cls(load_yaml(gateway_path), load_yaml(profile_path))

    def _load(self):
        if not self._profiles_root.get("enabled", True):
            raise RuntimeError("execution profiles are disabled")

        allowed = self._gateway.get("allowed_commands") or {}
        if not isinstance(allowed, dict):
            raise RuntimeError("gateway allowed_commands must be a mapping")

        command_profiles = self._profiles_root.get("command_profiles") or {}
        profiles = self._profiles_root.get("profiles") or {}

        # Union of yaml allow-list, sandbox, and research command specs.
        candidates = dict(allowed)
        for name, spec in SANDBOX_COMMAND_SPECS.items():
            candidates.setdefault(name, spec)
        for name, spec in RESEARCH_COMMAND_SPECS.items():
            candidates.setdefault(name, spec)

        if not candidates:
            raise RuntimeError("no candidate commands for capability registry")

        for command_name, command_spec in candidates.items():
            if not isinstance(command_spec, dict):
                raise ValueError(
                    f"command spec must be a mapping: {command_name}"
                )
            profile_name = command_profiles.get(command_name)
            if not profile_name:
                # Command without a profile is not yet a capability.
                continue
            profile = profiles.get(profile_name)
            if not isinstance(profile, dict):
                raise ValueError(
                    f"unknown or invalid profile {profile_name!r} "
                    f"for command {command_name}"
                )
            cap = build_capability(
                command_name,
                command_name,
                command_spec,
                profile_name,
                profile,
            )
            self._by_name[command_name] = cap

        for alias, command_name in CAPABILITY_ALIASES.items():
            if command_name in self._by_name:
                base = dict(self._by_name[command_name])
                base["name"] = alias
                base["aliased_to"] = command_name
                self._by_name[alias] = base

    def list_capabilities(self):
        return sorted(self._by_name.keys())

    def get(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("capability name must be a non-empty string")
        name = name.strip()
        if name in DISABLED_CAPABILITIES:
            return None
        return self._by_name.get(name)

    def require(self, name):
        """Return the capability or raise PermissionError (fail closed)."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("capability name must be a non-empty string")
        name = name.strip()
        if name in DISABLED_CAPABILITIES:
            raise PermissionError(
                f"capability disabled until approval boundary: {name}"
            )
        cap = self._by_name.get(name)
        if cap is None:
            raise PermissionError(f"unknown capability: {name}")
        return cap

    def validate_invocation(self, name, arguments=None):
        """Check name + basic argument shape. Does not execute."""
        cap = self.require(name)
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be a dictionary")

        required = cap["input_contract"].get("required") or []
        missing = [key for key in required if key not in arguments]
        if missing:
            raise ValueError(
                f"capability {cap['name']!r} missing arguments: {missing}"
            )

        project = arguments.get("project")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project must be a non-empty string")

        task_id = arguments.get("task_id")
        if task_id is not None and (
            isinstance(task_id, bool) or not isinstance(task_id, int)
        ):
            raise TypeError("task_id must be an integer or None")

        out_args = {
            "project": project.strip(),
            "task_id": task_id,
        }
        if cap["name"] == "research" or cap["command"] == "research":
            action = (arguments.get("action") or "fetch").strip().lower()
            if action not in ("fetch", "search"):
                raise ValueError("research action must be fetch or search")
            out_args["action"] = action
            if action == "fetch":
                url = arguments.get("url")
                if not isinstance(url, str) or not url.strip():
                    raise ValueError("research fetch requires url")
                out_args["url"] = url.strip()
            else:
                query = arguments.get("query")
                if not isinstance(query, str) or not query.strip():
                    raise ValueError("research search requires query")
                out_args["query"] = query.strip()

        return {
            "ok": True,
            "capability": cap["name"],
            "command": cap["command"],
            "profile": cap["resource_limit"]["profile"],
            "arguments": out_args,
        }

    def evaluate_request(
        self,
        name,
        arguments=None,
        *,
        db=None,
        task_id=None,
        goal_id=None,
    ):
        """Admit or deny a capability request and always audit when ``db`` is set.

        Returns a structured decision. Denials do not raise — callers must
        treat ``admitted is False`` as fail-closed.
        """
        arguments = {} if arguments is None else arguments
        request_meta = {
            "capability": name,
            "arguments": arguments if isinstance(arguments, dict) else None,
        }
        if db is not None:
            audit_capability_event(
                db,
                "CAPABILITY_REQUEST",
                f"capability request: {name}",
                request_meta,
                task_id=task_id,
                goal_id=goal_id,
            )

        try:
            validated = self.validate_invocation(name, arguments)
        except PermissionError as exc:
            reason = str(exc)
            denial_class = "disabled" if "disabled" in reason else "unknown"
            decision = {
                "admitted": False,
                "denied": True,
                "reason": reason,
                "denial_class": denial_class,
                "capability": name if isinstance(name, str) else None,
            }
            if db is not None:
                audit_capability_event(
                    db,
                    "CAPABILITY_DENIED",
                    f"capability denied: {name}",
                    decision,
                    task_id=task_id,
                    goal_id=goal_id,
                )
            return decision
        except (ValueError, TypeError) as exc:
            decision = {
                "admitted": False,
                "denied": True,
                "reason": str(exc),
                "denial_class": "contract",
                "capability": name if isinstance(name, str) else None,
            }
            if db is not None:
                audit_capability_event(
                    db,
                    "CAPABILITY_DENIED",
                    f"capability denied: {name}",
                    decision,
                    task_id=task_id,
                    goal_id=goal_id,
                )
            return decision

        gate_name = validated["capability"]
        if gate_name in APPROVAL_GATED_CAPABILITIES or (
            validated["command"] in APPROVAL_GATED_CAPABILITIES
        ):
            invoke_task = task_id or validated["arguments"].get("task_id")
            if db is None or invoke_task is None:
                decision = {
                    "admitted": False,
                    "denied": True,
                    "reason": (
                        f"capability {gate_name!r} requires approved "
                        "task_id + db"
                    ),
                    "denial_class": "approval",
                    "capability": gate_name,
                }
                if db is not None:
                    audit_capability_event(
                        db,
                        "CAPABILITY_DENIED",
                        f"capability denied: {gate_name}",
                        decision,
                        task_id=invoke_task,
                        goal_id=goal_id,
                    )
                return decision
            approval = _load_valid_approval(db, invoke_task, gate_name)
            if approval is None:
                decision = {
                    "admitted": False,
                    "denied": True,
                    "reason": (
                        f"capability {gate_name!r} requires human approval"
                    ),
                    "denial_class": "approval",
                    "capability": gate_name,
                }
                audit_capability_event(
                    db,
                    "CAPABILITY_DENIED",
                    f"capability denied: {gate_name}",
                    decision,
                    task_id=invoke_task,
                    goal_id=goal_id,
                )
                return decision

        decision = {
            "admitted": True,
            "denied": False,
            "capability": validated["capability"],
            "command": validated["command"],
            "profile": validated["profile"],
            "arguments": validated["arguments"],
        }
        if db is not None:
            audit_capability_event(
                db,
                "CAPABILITY_ADMITTED",
                f"capability admitted: {validated['capability']}",
                decision,
                task_id=task_id or validated["arguments"].get("task_id"),
                goal_id=goal_id,
            )
        return decision

    def invoke(
        self,
        name,
        arguments=None,
        *,
        db=None,
        task_id=None,
        goal_id=None,
        gateway_bin=None,
    ):
        """Evaluate then run the gateway command (V0.6.2 step 3).

        Profile timeout/output limits are enforced by the gateway ``run``
        path (step 4). Host git uses hardened env/argv (F4).
        """
        decision = self.evaluate_request(
            name,
            arguments,
            db=db,
            task_id=task_id,
            goal_id=goal_id,
        )
        if not decision.get("admitted"):
            return {
                "ok": False,
                "admitted": False,
                "decision": decision,
            }

        command = decision["command"]
        project = decision["arguments"]["project"]
        invoke_task_id = (
            task_id
            if task_id is not None
            else decision["arguments"].get("task_id")
        )

        if command == "research":
            result = _invoke_research(
                decision,
                db=db,
                task_id=invoke_task_id,
                goal_id=goal_id,
            )
            if db is not None:
                audit_capability_event(
                    db,
                    "CAPABILITY_RESULT",
                    (
                        f"capability result: {name} "
                        f"ok={result.get('ok')}"
                    ),
                    {
                        "capability": name,
                        "command": command,
                        "project": project,
                        "ok": result.get("ok"),
                        "error": result.get("error"),
                        "profile": decision.get("profile"),
                        "research_id": (result.get("research_result") or {}).get(
                            "research_id"
                        ),
                    },
                    task_id=invoke_task_id,
                    goal_id=goal_id,
                )
            return result

        gateway = Path(gateway_bin) if gateway_bin else None
        if gateway is None or not gateway.is_file():
            for candidate in (
                GATEWAY_BIN,
                _HERE / "anu-command-gateway.py",
                _REPO / "bin" / "anu-command-gateway.py",
            ):
                if candidate.is_file():
                    gateway = candidate
                    break
        if gateway is None or not gateway.is_file():
            result = {
                "ok": False,
                "admitted": True,
                "error": "command gateway binary not found",
                "decision": decision,
            }
            if db is not None:
                audit_capability_event(
                    db,
                    "CAPABILITY_RESULT",
                    f"capability invoke failed: {name}",
                    result,
                    task_id=invoke_task_id,
                    goal_id=goal_id,
                )
            return result

        argv = [
            sys.executable,
            str(gateway),
            "run",
            command,
            "--project",
            project,
        ]
        if invoke_task_id is not None:
            argv.extend(["--task-id", str(invoke_task_id)])

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            parsed = None
            try:
                parsed = json.loads(stdout)
            except Exception:
                parsed = None
            ok = bool(parsed and parsed.get("ok")) if parsed else (
                proc.returncode == 0
            )
            result = {
                "ok": ok,
                "admitted": True,
                "decision": decision,
                "gateway_exit_code": proc.returncode,
                "gateway_result": parsed,
                "stdout": stdout,
                "stderr": stderr,
            }
        except Exception as exc:
            result = {
                "ok": False,
                "admitted": True,
                "error": str(exc),
                "decision": decision,
            }

        if db is not None:
            audit_capability_event(
                db,
                "CAPABILITY_RESULT",
                (
                    f"capability result: {name} "
                    f"ok={result.get('ok')}"
                ),
                {
                    "capability": name,
                    "command": command,
                    "project": project,
                    "ok": result.get("ok"),
                    "gateway_exit_code": result.get("gateway_exit_code"),
                    "error": result.get("error"),
                    "profile": decision.get("profile"),
                },
                task_id=invoke_task_id,
                goal_id=goal_id,
            )
        return result


def _invoke_research(decision, *, db=None, task_id=None, goal_id=None):
    """Run anu-research-v0.1.py as a host process (not gateway/sandbox)."""
    del goal_id  # audited by caller
    research_bin = _research_bin()
    if research_bin is None:
        return {
            "ok": False,
            "admitted": True,
            "error": "anu-research-v0.1.py not found",
            "decision": decision,
        }
    args = decision.get("arguments") or {}
    action = args.get("action") or "fetch"
    argv = [sys.executable, "-B", str(research_bin), action]
    if action == "fetch":
        argv.extend(["--url", args["url"]])
    else:
        argv.extend(["--query", args["query"]])
    if task_id is not None:
        argv.extend(["--task-id", str(task_id)])
    if db is not None:
        argv.append("--persist")
        # Prefer the live connection's file when available.
        db_path = None
        try:
            rows = db.execute("PRAGMA database_list").fetchall()
            for row in rows:
                # sqlite3.Row or tuple: (seq, name, file)
                name = row[1] if not isinstance(row, sqlite3.Row) else row["name"]
                path = row[2] if not isinstance(row, sqlite3.Row) else row["file"]
                if name == "main" and path:
                    db_path = path
                    break
        except Exception:
            db_path = None
        if db_path:
            argv.extend(["--db", str(db_path)])

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        parsed = None
        try:
            parsed = json.loads(stdout)
        except Exception:
            parsed = None
        ok = bool(parsed and parsed.get("ok")) if parsed else (
            proc.returncode == 0
        )
        return {
            "ok": ok,
            "admitted": True,
            "decision": decision,
            "gateway_exit_code": proc.returncode,
            "research_result": parsed,
            "stdout": stdout,
            "stderr": stderr,
        }
    except Exception as exc:
        return {
            "ok": False,
            "admitted": True,
            "error": str(exc),
            "decision": decision,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ANU Capability Registry v0.1"
    )
    parser.add_argument("--gateway-config", default=None)
    parser.add_argument("--profile-config", default=None)
    parser.add_argument("--db", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")
    get_p = sub.add_parser("get")
    get_p.add_argument("--name", required=True)

    req_p = sub.add_parser("require")
    req_p.add_argument("--name", required=True)

    research_args = argparse.ArgumentParser(add_help=False)
    research_args.add_argument(
        "--action", default=None, help="research: fetch|search"
    )
    research_args.add_argument("--url", default=None, help="research fetch URL")
    research_args.add_argument(
        "--query", default=None, help="research search query"
    )

    val_p = sub.add_parser("validate", parents=[research_args])
    val_p.add_argument("--name", required=True)
    val_p.add_argument("--project", required=True)
    val_p.add_argument("--task-id", type=int, default=None)

    admit_p = sub.add_parser("admit", parents=[research_args])
    admit_p.add_argument("--name", required=True)
    admit_p.add_argument("--project", required=True)
    admit_p.add_argument("--task-id", type=int, default=None)
    admit_p.add_argument("--goal-id", type=int, default=None)

    inv_p = sub.add_parser("invoke", parents=[research_args])
    inv_p.add_argument("--name", required=True)
    inv_p.add_argument("--project", required=True)
    inv_p.add_argument("--task-id", type=int, default=None)
    inv_p.add_argument("--goal-id", type=int, default=None)

    args = parser.parse_args(argv)
    registry = CapabilityRegistry.from_files(
        args.gateway_config,
        args.profile_config,
    )

    if args.cmd == "list":
        print(json.dumps({
            "ok": True,
            "capabilities": registry.list_capabilities(),
        }, indent=2))
        return 0
    if args.cmd == "get":
        cap = registry.get(args.name)
        if cap is None:
            print(json.dumps({"ok": False, "error": "not_found"}))
            return 1
        print(json.dumps({"ok": True, "capability": cap}, indent=2))
        return 0
    if args.cmd == "require":
        try:
            cap = registry.require(args.name)
        except PermissionError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
        print(json.dumps({"ok": True, "capability": cap}, indent=2))
        return 0
    def _cli_arguments():
        payload = {"project": args.project, "task_id": args.task_id}
        if getattr(args, "action", None):
            payload["action"] = args.action
        if getattr(args, "url", None):
            payload["url"] = args.url
        if getattr(args, "query", None):
            payload["query"] = args.query
        return payload

    if args.cmd == "validate":
        try:
            result = registry.validate_invocation(
                args.name,
                _cli_arguments(),
            )
        except (PermissionError, ValueError, TypeError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
        print(json.dumps({"ok": True, "result": result}, indent=2))
        return 0
    if args.cmd in ("admit", "invoke"):
        db = None
        db_path = args.db or (str(DB_FILE) if DB_FILE.is_file() else None)
        if db_path:
            try:
                for candidate in (
                    BASE / "bin" / "anu-db-v0.1.py",
                    _HERE / "anu-db-v0.1.py",
                ):
                    if candidate.is_file():
                        spec = importlib.util.spec_from_file_location(
                            "anu_db_v0_1_cap_cli",
                            candidate,
                        )
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        db = mod.db_connect(db_path)
                        break
            except Exception:
                db = None
            if db is None:
                db = sqlite3.connect(db_path)
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys = ON")
        try:
            if args.cmd == "admit":
                decision = registry.evaluate_request(
                    args.name,
                    _cli_arguments(),
                    db=db,
                    task_id=args.task_id,
                    goal_id=args.goal_id,
                )
                print(json.dumps({"ok": True, "decision": decision}, indent=2))
                return 0 if decision.get("admitted") else 1
            result = registry.invoke(
                args.name,
                _cli_arguments(),
                db=db,
                task_id=args.task_id,
                goal_id=args.goal_id,
            )
            print(json.dumps({"ok": bool(result.get("ok")), "result": result}, indent=2))
            return 0 if result.get("ok") else 1
        finally:
            if db is not None:
                db.close()

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
