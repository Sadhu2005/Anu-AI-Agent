#!/usr/bin/env python3

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


BASE = Path("/srv/anu")
CONFIG_FILE = BASE / "config" / "anu-executor.yaml"
DB_FILE = BASE / "brain" / "anu-brain.db"
LOG_FILE = BASE / "logs" / "anu-actions.jsonl"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_config():
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


CONFIG = load_config()

WORKSPACE = Path(
    CONFIG["workspace"]["root"]
).resolve()

LIMITS = CONFIG.get("limits", {})
OPERATIONS = CONFIG.get("operations", {})


def fail(message, code=1):
    event = {
        "timestamp": utc_now(),
        "argv": sys.argv[1:],
        "error": message,
    }

    try:
        log_action(
            "request",
            "denied",
            details=event,
        )
    except Exception:
        pass

    result = {
        "ok": False,
        "error": message,
        "timestamp": utc_now(),
    }

    print(json.dumps(result, indent=2))
    sys.exit(code)


def validate_operation(name):
    if not OPERATIONS.get(name, False):
        fail(f"Operation not allowed: {name}")


def safe_path(user_path):
    """
    Convert a user-provided path into a real path and ensure
    it remains inside ANU's project workspace.
    """

    if not user_path:
        fail("Path is required")

    path = Path(user_path)

    # Do not accept absolute paths.
    if path.is_absolute():
        fail("Absolute paths are not allowed")

    # Reject obvious traversal.
    if ".." in path.parts:
        fail("Path traversal is not allowed")

    target = (WORKSPACE / path).resolve()

    try:
        target.relative_to(WORKSPACE)
    except ValueError:
        fail("Path is outside ANU workspace")

    return target


def log_action(
    operation,
    status,
    path=None,
    details=None,
):
    event = {
        "timestamp": utc_now(),
        "agent": "ANU",
        "operation": operation,
        "status": status,
        "path": str(path) if path else None,
        "details": details or {},
    }

    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

    try:
        connection = sqlite3.connect(DB_FILE)

        connection.execute(
            """
            INSERT INTO agent_events
            (
                event_type,
                message,
                metadata
            )
            VALUES (?, ?, ?)
            """,
            (
                "EXECUTOR_ACTION",
                f"{operation}: {status}",
                json.dumps(event),
            ),
        )

        connection.commit()
        connection.close()

    except Exception:
        # Executor logging must never crash the requested filesystem operation.
        pass


def cmd_status():
    result = {
        "ok": True,
        "agent": "ANU",
        "executor": "0.1.0",
        "workspace": str(WORKSPACE),
        "operations": OPERATIONS,
        "limits": LIMITS,
        "timestamp": utc_now(),
    }

    print(json.dumps(result, indent=2))


def cmd_list(path):
    validate_operation("list")

    target = safe_path(path)

    if not target.exists():
        fail("Path does not exist")

    if not target.is_dir():
        fail("Path is not a directory")

    entries = sorted(
        target.iterdir(),
        key=lambda p: p.name.lower()
    )

    max_entries = int(LIMITS.get("max_list_entries", 200))
    entries = entries[:max_entries]

    result = []

    for entry in entries:
        result.append(
            {
                "name": entry.name,
                "type": (
                    "directory"
                    if entry.is_dir()
                    else "file"
                    if entry.is_file()
                    else "other"
                ),
            }
        )

    log_action(
        "list",
        "success",
        target,
        {"count": len(result)},
    )

    print(
        json.dumps(
            {
                "ok": True,
                "path": str(target),
                "entries": result,
            },
            indent=2,
        )
    )


def cmd_read(path):
    validate_operation("read")

    target = safe_path(path)

    if not target.exists():
        fail("File does not exist")

    if not target.is_file():
        fail("Target is not a regular file")

    max_bytes = int(
        LIMITS.get("max_read_bytes", 262144)
    )

    size = target.stat().st_size

    if size > max_bytes:
        fail(
            f"File is too large: {size} bytes "
            f"(limit {max_bytes})"
        )

    content = target.read_text(
        encoding="utf-8",
        errors="replace",
    )

    log_action(
        "read",
        "success",
        target,
        {"bytes": size},
    )

    print(
        json.dumps(
            {
                "ok": True,
                "path": str(target),
                "bytes": size,
                "content": content,
            },
            indent=2,
        )
    )


def cmd_write(path):
    validate_operation("write")

    target = safe_path(path)

    if target.exists() and target.is_dir():
        fail("Cannot write to a directory")

    parent = target.parent

    if not parent.exists():
        fail(
            "Parent directory does not exist. "
            "Create it with mkdir first."
        )

    content = sys.stdin.read()

    max_bytes = int(
        LIMITS.get("max_write_bytes", 524288)
    )

    content_bytes = content.encode("utf-8")

    if len(content_bytes) > max_bytes:
        fail(
            f"Content is too large: "
            f"{len(content_bytes)} bytes "
            f"(limit {max_bytes})"
        )

    temp = target.with_name(
        f".{target.name}.anu.tmp"
    )

    try:
        with temp.open(
            "w",
            encoding="utf-8",
        ) as f:
            f.write(content)

        os.replace(temp, target)

    finally:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass

    log_action(
        "write",
        "success",
        target,
        {"bytes": len(content_bytes)},
    )

    print(
        json.dumps(
            {
                "ok": True,
                "path": str(target),
                "bytes": len(content_bytes),
            },
            indent=2,
        )
    )


def cmd_mkdir(path):
    validate_operation("mkdir")

    target = safe_path(path)

    if target.exists():
        fail("Path already exists")

    target.mkdir(parents=True, exist_ok=False)

    log_action(
        "mkdir",
        "success",
        target,
    )

    print(
        json.dumps(
            {
                "ok": True,
                "path": str(target),
            },
            indent=2,
        )
    )


def cmd_delete_file(path):
    validate_operation("delete_file")

    target = safe_path(path)

    if not target.exists():
        fail("Path does not exist")

    if not target.is_file():
        fail("Target is not a regular file")

    target.unlink()

    log_action(
        "delete_file",
        "success",
        target,
    )

    print(
        json.dumps(
            {
                "ok": True,
                "path": str(target),
            },
            indent=2,
        )
    )


def cmd_delete_empty_directory(path):
    validate_operation("delete_empty_directory")

    target = safe_path(path)

    if not target.exists():
        fail("Path does not exist")

    if not target.is_dir():
        fail("Target is not a directory")

    target.rmdir()

    log_action(
        "delete_empty_directory",
        "success",
        target,
    )

    print(
        json.dumps(
            {
                "ok": True,
                "path": str(target),
            },
            indent=2,
        )
    )


def cmd_search(pattern, path):
    validate_operation("search")

    root = safe_path(path)

    if not root.exists():
        fail("Search path does not exist")

    max_results = int(
        LIMITS.get("max_search_results", 100)
    )

    max_files = int(
        LIMITS.get("max_search_files", 5000)
    )

    results = []
    scanned = 0

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        fail(f"Invalid regular expression: {exc}")

    files = (
        [root]
        if root.is_file()
        else root.rglob("*")
    )

    for candidate in files:

        if scanned >= max_files:
            break

        if not candidate.is_file():
            continue

        try:
            candidate.resolve().relative_to(WORKSPACE)
        except ValueError:
            continue

        scanned += 1

        try:
            with candidate.open(
                "r",
                encoding="utf-8",
                errors="ignore",
            ) as f:
                for line_number, line in enumerate(
                    f,
                    start=1,
                ):
                    if regex.search(line):
                        results.append(
                            {
                                "file": str(candidate),
                                "line": line_number,
                                "text": line.rstrip()[:500],
                            }
                        )

                        if len(results) >= max_results:
                            break

        except (OSError, UnicodeError):
            continue

        if len(results) >= max_results:
            break

    log_action(
        "search",
        "success",
        root,
        {
            "pattern": pattern,
            "scanned_files": scanned,
            "results": len(results),
        },
    )

    print(
        json.dumps(
            {
                "ok": True,
                "pattern": pattern,
                "results": results,
                "scanned_files": scanned,
            },
            indent=2,
        )
    )


def main():
    os.umask(0o077)

    parser = argparse.ArgumentParser(
        prog="anu-executor"
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sub.add_parser("status")

    p = sub.add_parser("list")
    p.add_argument("path")

    p = sub.add_parser("read")
    p.add_argument("path")

    p = sub.add_parser("write")
    p.add_argument("path")

    p = sub.add_parser("mkdir")
    p.add_argument("path")

    p = sub.add_parser("delete-file")
    p.add_argument("path")

    p = sub.add_parser("delete-empty-directory")
    p.add_argument("path")

    p = sub.add_parser("search")
    p.add_argument("pattern")
    p.add_argument("path")

    args = parser.parse_args()

    try:
        if args.command == "status":
            cmd_status()

        elif args.command == "list":
            cmd_list(args.path)

        elif args.command == "read":
            cmd_read(args.path)

        elif args.command == "write":
            cmd_write(args.path)

        elif args.command == "mkdir":
            cmd_mkdir(args.path)

        elif args.command == "delete-file":
            cmd_delete_file(args.path)

        elif args.command == "delete-empty-directory":
            cmd_delete_empty_directory(args.path)

        elif args.command == "search":
            cmd_search(args.pattern, args.path)

        else:
            fail("Unknown command")

    except PermissionError as exc:
        fail(f"Permission denied: {exc}")

    except OSError as exc:
        fail(f"Operating system error: {exc}")

    except Exception as exc:
        fail(f"Executor error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
