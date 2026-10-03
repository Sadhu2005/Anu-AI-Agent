#!/usr/bin/env python3

import json
import os
import signal
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

import yaml


BASE = Path("/srv/anu")

CONFIG_FILE = BASE / "config" / "anu-sandbox.yaml"
SOCKET_FILE = Path("/run/anu-sandbox/sandbox.sock")

MAX_REQUEST_BYTES = 64 * 1024
BUFFER_SIZE = 4096

STOP = False
EXECUTION_LOCK = threading.Lock()


def load_config():
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


CONFIG = load_config()

SANDBOX = CONFIG["sandbox"]
DOCKER = CONFIG["docker"]
WORKSPACE = Path(
    CONFIG["workspace"]["root"]
).resolve()
OPERATIONS = CONFIG["operations"]
ADMISSION = CONFIG["admission"]

AUTHORIZED_UID = 999
AUTHORIZED_GID = 984

GOVERNOR = Path(
    ADMISSION["governor"]
).resolve()


def send_json(conn, payload):
    raw = (
        json.dumps(
            payload,
            separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")

    conn.sendall(raw)


def peer_credentials(conn):
    size = struct.calcsize("3i")

    raw = conn.getsockopt(
        socket.SOL_SOCKET,
        socket.SO_PEERCRED,
        size,
    )

    pid, uid, gid = struct.unpack(
        "3i",
        raw,
    )

    return pid, uid, gid


def read_request(conn):
    data = bytearray()

    while len(data) < MAX_REQUEST_BYTES:
        chunk = conn.recv(BUFFER_SIZE)

        if not chunk:
            break

        data.extend(chunk)

        if b"\n" in chunk:
            break

    if len(data) >= MAX_REQUEST_BYTES:
        raise ValueError(
            "Request exceeds maximum size."
        )

    line = bytes(data).split(
        b"\n",
        1,
    )[0]

    if not line:
        raise ValueError(
            "Empty request."
        )

    request = json.loads(
        line.decode("utf-8")
    )

    if not isinstance(request, dict):
        raise ValueError(
            "Request must be a JSON object."
        )

    return request


def safe_project(project):
    if not isinstance(project, str):
        raise ValueError(
            "Project must be a string."
        )

    if not project:
        raise ValueError(
            "Project is required."
        )

    relative = Path(project)

    if relative.is_absolute():
        raise ValueError(
            "Absolute project paths are not allowed."
        )

    if ".." in relative.parts:
        raise ValueError(
            "Path traversal is not allowed."
        )

    target = (
        WORKSPACE / relative
    ).resolve()

    try:
        target.relative_to(WORKSPACE)
    except ValueError:
        raise ValueError(
            "Project is outside ANU workspace."
        )

    if not target.is_dir():
        raise ValueError(
            "Project directory does not exist."
        )

    return target


def resource_admission():
    if not ADMISSION.get("enabled", False):
        raise RuntimeError(
            "Sandbox resource admission is disabled."
        )

    ram_mb = float(
        ADMISSION["ram_mb"]
    )

    storage_mb = float(
        ADMISSION["storage_mb"]
    )

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
            "Resource Governor failed."
        )

    try:
        decision = json.loads(
            result.stdout
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid Resource Governor response: {exc}"
        )

    decision_name = decision.get(
        "decision"
    )

    if decision_name != ADMISSION.get(
        "allow_decision",
        "ALLOW",
    ):
        raise PermissionError(
            "Sandbox resource admission denied: "
            + json.dumps(
                decision,
                separators=(",", ":"),
            )
        )

    return decision


def build_command(
    operation,
    project_path,
):
    operation_cfg = OPERATIONS.get(
        operation
    )

    if not operation_cfg:
        raise ValueError(
            f"Operation not allowed: {operation}"
        )

    image = operation_cfg.get(
        "image"
    )

    if image != DOCKER["image"]:
        raise ValueError(
            "Operation image does not match pinned sandbox image."
        )

    command = operation_cfg.get(
        "command"
    )

    if not isinstance(command, list):
        raise ValueError(
            "Sandbox command configuration invalid."
        )

    if not command:
        raise ValueError(
            "Sandbox command cannot be empty."
        )

    if not all(
        isinstance(item, str)
        for item in command
    ):
        raise ValueError(
            "Sandbox command contains invalid arguments."
        )

    container_name = (
        f"anu-sbx-{os.getpid()}-"
        f"{time.time_ns()}"
    )

    project_mount = (
        f"{project_path}:/workspace:ro"
    )

    argv = [
        DOCKER["executable"],
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        DOCKER["network"],
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        str(DOCKER["pids_limit"]),
        "--cpus",
        str(DOCKER["cpus"]),
        "--memory",
        f"{DOCKER['memory_mb']}m",
        "--memory-swap",
        f"{DOCKER['memory_swap_mb']}m",
        "--tmpfs",
        (
            f"/tmp:rw,nosuid,nodev,noexec,"
            f"size={DOCKER['tmpfs_size_mb']}m"
        ),
        "--user",
        DOCKER["user"],
        "-v",
        project_mount,
        "-w",
        DOCKER["workdir"],
        image,
        *command,
    ]

    return container_name, argv


def cleanup_container(container_name):
    try:
        subprocess.run(
            [
                DOCKER["executable"],
                "rm",
                "-f",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
    except Exception:
        pass


def execute(request):
    operation = request.get(
        "operation"
    )

    project = request.get(
        "project"
    )

    if not isinstance(operation, str):
        raise ValueError(
            "Operation must be a string."
        )

    if operation not in OPERATIONS:
        raise ValueError(
            "Unsupported sandbox operation."
        )

    project_path = safe_project(
        project
    )

    admission = resource_admission()

    container_name, argv = build_command(
        operation,
        project_path,
    )

    timeout_seconds = int(
        CONFIG["execution"]["timeout_seconds"]
    )

    max_output_bytes = int(
        CONFIG["execution"]["max_output_bytes"]
    )

    started = time.monotonic()

    try:
        process = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        cleanup_container(
            container_name
        )

        raise RuntimeError(
            "Sandbox execution timed out; "
            "container cleanup attempted."
        )

    elapsed = (
        time.monotonic()
        - started
    )

    stdout = process.stdout.decode(
        "utf-8",
        errors="replace",
    )

    stderr = process.stderr.decode(
        "utf-8",
        errors="replace",
    )

    output = stdout

    if stderr:
        output += stderr

    raw_output = output.encode(
        "utf-8"
    )

    truncated = False

    if len(raw_output) > max_output_bytes:
        output = raw_output[
            :max_output_bytes
        ].decode(
            "utf-8",
            errors="replace",
        )

        truncated = True

    return {
        "ok": process.returncode == 0,
        "operation": operation,
        "project": str(project_path),
        "exit_code": process.returncode,
        "elapsed_seconds": round(
            elapsed,
            3,
        ),
        "output": output,
        "output_truncated": truncated,
        "resource_admission": admission,
        "sandbox": {
            "image": DOCKER["image"],
            "network": DOCKER["network"],
            "memory_mb": DOCKER["memory_mb"],
            "memory_swap_mb": DOCKER[
                "memory_swap_mb"
            ],
            "cpus": DOCKER["cpus"],
            "pids_limit": DOCKER[
                "pids_limit"
            ],
            "user": DOCKER["user"],
            "project_mount": "read-only",
            "workdir": DOCKER["workdir"],
            "read_only_rootfs": True,
            "cap_drop_all": True,
            "no_new_privileges": True,
        },
    }


def handle_client(conn):
    try:
        pid, uid, gid = peer_credentials(
            conn
        )

        if (
            uid != AUTHORIZED_UID
            or gid != AUTHORIZED_GID
        ):
            send_json(
                conn,
                {
                    "ok": False,
                    "error": (
                        "Peer credentials rejected."
                    ),
                    "peer": {
                        "pid": pid,
                        "uid": uid,
                        "gid": gid,
                    },
                },
            )
            return

        if not EXECUTION_LOCK.acquire(
            blocking=False
        ):
            send_json(
                conn,
                {
                    "ok": False,
                    "error": (
                        "Sandbox is busy; "
                        "maximum parallel "
                        "tasks reached."
                    ),
                },
            )
            return

        try:
            request = read_request(
                conn
            )

            result = execute(
                request
            )

            send_json(
                conn,
                result
            )

        finally:
            EXECUTION_LOCK.release()

    except Exception as exc:
        try:
            send_json(
                conn,
                {
                    "ok": False,
                    "error": str(exc),
                },
            )
        except Exception:
            pass

    finally:
        conn.close()


def shutdown(signum, frame):
    global STOP
    STOP = True

    try:
        SOCKET_FILE.unlink()
    except FileNotFoundError:
        pass


def main():
    global STOP

    if os.geteuid() != 0:
        raise SystemExit(
            "Sandbox daemon must run as root."
        )

    if not SANDBOX.get(
        "enabled",
        False,
    ):
        raise SystemExit(
            "Sandbox is disabled."
        )

    if (
        DOCKER["privileged"]
        is True
    ):
        raise SystemExit(
            "Privileged sandbox is forbidden."
        )

    if DOCKER["network"] != "none":
        raise SystemExit(
            "Sandbox network must be none."
        )

    if DOCKER["user"] != (
        f"{AUTHORIZED_UID}:{AUTHORIZED_GID}"
    ):
        raise SystemExit(
            "Sandbox UID/GID policy mismatch."
        )

    SOCKET_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    os.chown(
        SOCKET_FILE.parent,
        0,
        AUTHORIZED_GID,
    )

    os.chmod(
        SOCKET_FILE.parent,
        0o750,
    )

    try:
        SOCKET_FILE.unlink()
    except FileNotFoundError:
        pass

    signal.signal(
        signal.SIGTERM,
        shutdown,
    )

    signal.signal(
        signal.SIGINT,
        shutdown,
    )

    server = socket.socket(
        socket.AF_UNIX,
        socket.SOCK_STREAM,
    )

    server.bind(
        str(SOCKET_FILE)
    )

    os.chown(
        SOCKET_FILE,
        0,
        AUTHORIZED_GID,
    )

    os.chmod(
        SOCKET_FILE,
        0o660,
    )

    server.listen(8)
    server.settimeout(1.0)

    print(
        f"ANU Sandbox listening on {SOCKET_FILE}",
        flush=True,
    )

    while not STOP:
        try:
            conn, _ = server.accept()
        except socket.timeout:
            continue

        thread = threading.Thread(
            target=handle_client,
            args=(conn,),
            daemon=True,
        )

        thread.start()

    server.close()

    try:
        SOCKET_FILE.unlink()
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    main()
