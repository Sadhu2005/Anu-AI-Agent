#!/usr/bin/env python3
"""Fail if CONFIGURATION.md claims keys that do not appear in the owning module.

Heuristic: for a small hardcoded map of config file → reader module, require
that documented key path strings (or obvious YAML leaf names) occur in the
module source. Keeps docs honest without parsing full YAML schemas.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CONFIG_DOC = REPO / "docs" / "CONFIGURATION.md"

# config stem → (bin module, keys that must appear in module source)
KEY_MODULE_MAP: dict[str, tuple[str, tuple[str, ...]]] = {
    "anu-resources.yaml": (
        "bin/anu-resource-guardian.py",
        (
            "anu-resources.yaml",
            "temperature.warning_celsius",
            "temperature.pause_celsius",
            "memory.emergency_free_mb",
            "storage.minimum_free_gb",
        ),
    ),
    "anu-admission-policy.yaml": (
        "bin/anu-resource-governor.py",
        (
            "anu-admission-policy.yaml",
            "host_safety",
            "anu_budget",
        ),
    ),
    "anu-command-gateway.yaml": (
        "bin/anu-command-gateway.py",
        (
            "anu-command-gateway.yaml",
            "python_test",
            "git_status",
        ),
    ),
    "anu-execution-profiles.yaml": (
        "bin/anu-execution-profile.py",
        (
            "anu-execution-profiles.yaml",
            "profiles",
        ),
    ),
    "anu-sandbox.yaml": (
        "bin/anu-sandboxd.py",
        (
            "anu-sandbox.yaml",
            "operations",
        ),
    ),
    "anu-executor.yaml": (
        "bin/anu-executor.py",
        (
            "anu-executor.yaml",
        ),
    ),
}


def doc_mentions_config(config_name: str, doc_text: str) -> bool:
    return config_name in doc_text


def key_in_source(key: str, source: str) -> bool:
    if key in source:
        return True
    # Allow dotted keys if all segments appear (e.g. temperature + warning_celsius).
    parts = key.split(".")
    if len(parts) > 1:
        return all(part in source for part in parts)
    return False


def check_pair(config_name: str, module_rel: str, keys: tuple[str, ...]) -> list[str]:
    module_path = REPO / module_rel
    if not module_path.is_file():
        return [f"missing module {module_rel} for {config_name}"]
    source = module_path.read_text(encoding="utf-8")
    errors: list[str] = []
    if config_name not in source:
        errors.append(f"{module_rel} does not reference {config_name}")
    for key in keys:
        if key == config_name:
            continue
        if not key_in_source(key, source):
            errors.append(f"{config_name} key `{key}` not found in {module_rel}")
    return errors


def main() -> int:
    if not CONFIG_DOC.is_file():
        print(f"MISSING: {CONFIG_DOC}", file=sys.stderr)
        return 1

    doc_text = CONFIG_DOC.read_text(encoding="utf-8")
    failures: list[str] = []

    for config_name, (module_rel, keys) in KEY_MODULE_MAP.items():
        if not doc_mentions_config(config_name, doc_text):
            continue
        failures.extend(check_pair(config_name, module_rel, keys))

    if failures:
        print("CONFIG_DOC_DRIFT detected:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("CONFIG_DOC_DRIFT ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
