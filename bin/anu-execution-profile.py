#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

import yaml


BASE = Path("/srv/anu")

CONFIG_FILE = (
    BASE / "config" / "anu-execution-profiles.yaml"
)


def load_config():
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_profile(command_name: str):
    config = load_config()

    if not config.get("execution_profiles", {}).get("enabled", False):
        return {
            "ok": False,
            "error": "Execution profiles are disabled."
        }

    root = config["execution_profiles"]

    command_profiles = root.get(
        "command_profiles",
        {}
    )

    profiles = root.get(
        "profiles",
        {}
    )

    profile_name = command_profiles.get(
        command_name,
        root.get("default_profile")
    )

    if not profile_name:
        return {
            "ok": False,
            "error": "No execution profile configured."
        }

    profile = profiles.get(profile_name)

    if not profile:
        return {
            "ok": False,
            "error": f"Unknown execution profile: {profile_name}"
        }

    return {
        "ok": True,
        "command": command_name,
        "profile": profile_name,
        "settings": profile,
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        help="Gateway command name"
    )

    args = parser.parse_args()

    result = resolve_profile(args.command)

    print(
        json.dumps(
            result,
            indent=2
        )
    )

    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
