#!/usr/bin/env python3

import argparse
import json
import socket
import sys


SOCKET_FILE = "/run/anu-sandbox/sandbox.sock"
MAX_RESPONSE_BYTES = 256 * 1024


def main():
    parser = argparse.ArgumentParser(
        description="ANU Sandbox Client"
    )

    parser.add_argument(
        "--operation",
        required=True,
        choices=[
            "python_test",
            "python_compile",
        ],
    )

    parser.add_argument(
        "--project",
        required=True,
    )

    args = parser.parse_args()

    request = {
        "operation": args.operation,
        "project": args.project,
    }

    sock = socket.socket(
        socket.AF_UNIX,
        socket.SOCK_STREAM,
    )

    try:
        sock.connect(SOCKET_FILE)

        payload = (
            json.dumps(request)
            + "\n"
        ).encode("utf-8")

        sock.sendall(payload)

        data = bytearray()

        while len(data) < MAX_RESPONSE_BYTES:
            chunk = sock.recv(4096)

            if not chunk:
                break

            data.extend(chunk)

            if b"\n" in chunk:
                break

        if not data:
            raise RuntimeError(
                "Sandbox returned no response."
            )

        line = bytes(data).split(
            b"\n",
            1
        )[0]

        result = json.loads(
            line.decode("utf-8")
        )

        print(
            json.dumps(
                result,
                indent=2
            )
        )

        if not result.get("ok", False):
            sys.exit(1)

    finally:
        sock.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                },
                indent=2,
            )
        )
        sys.exit(1)
