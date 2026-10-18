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
import json
import sys
from pathlib import Path

import yaml


BASE = Path("/srv/anu")
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent

GATEWAY_CONFIG = BASE / "config" / "anu-command-gateway.yaml"
PROFILE_CONFIG = BASE / "config" / "anu-execution-profiles.yaml"

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

# research / browser stay unregistered until V0.6.7 approval exists.
DISABLED_CAPABILITIES = frozenset({"research", "browser"})


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

        # Union of yaml allow-list and profiled sandbox commands.
        candidates = dict(allowed)
        for name, spec in SANDBOX_COMMAND_SPECS.items():
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

        return {
            "ok": True,
            "capability": cap["name"],
            "command": cap["command"],
            "profile": cap["resource_limit"]["profile"],
            "arguments": {
                "project": project.strip(),
                "task_id": task_id,
            },
        }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="ANU Capability Registry v0.1"
    )
    parser.add_argument("--gateway-config", default=None)
    parser.add_argument("--profile-config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")
    get_p = sub.add_parser("get")
    get_p.add_argument("--name", required=True)

    req_p = sub.add_parser("require")
    req_p.add_argument("--name", required=True)

    val_p = sub.add_parser("validate")
    val_p.add_argument("--name", required=True)
    val_p.add_argument("--project", required=True)
    val_p.add_argument("--task-id", type=int, default=None)

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
    if args.cmd == "validate":
        try:
            result = registry.validate_invocation(
                args.name,
                {"project": args.project, "task_id": args.task_id},
            )
        except (PermissionError, ValueError, TypeError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
        print(json.dumps({"ok": True, "result": result}, indent=2))
        return 0

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
