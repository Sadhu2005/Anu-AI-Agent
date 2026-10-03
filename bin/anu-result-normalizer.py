#!/usr/bin/env python3

import json
import sys
from datetime import datetime, timezone


def build(operation, status, success, reason, output):
    return {
        "ok": success,
        "operation": operation,
        "status": status,
        "success": success,
        "reason": reason,
        "output": output,
        "normalized_at": datetime.now(timezone.utc).isoformat(),
    }


def normalize(result):
    operation = (
        result.get("sandbox_operation")
        or result.get("operation")
        or "unknown"
    )

    exit_code = result.get("exit_code")
    output = result.get("output") or ""
    error = result.get("error") or ""

    combined = f"{output}\n{error}".strip()

    # ---------------------------------------------------------
    # Resource admission
    # ---------------------------------------------------------
    admission = result.get("resource_admission") or {}

    if admission.get("decision") == "DENY":
        reason = "; ".join(admission.get("reasons", []))
        if not reason:
            reason = "Resource admission denied"

        return build(
            operation,
            "RESOURCE_DENIED",
            False,
            reason,
            combined,
        )

    sandbox_admission = result.get("sandbox_resource_admission") or {}

    if sandbox_admission.get("decision") == "DENY":
        reason = "; ".join(sandbox_admission.get("reasons", []))
        if not reason:
            reason = "Sandbox resource admission denied"

        return build(
            operation,
            "RESOURCE_DENIED",
            False,
            reason,
            combined,
        )

    # ---------------------------------------------------------
    # Security rejection
    # ---------------------------------------------------------
    if "Peer credentials rejected" in combined:
        return build(
            operation,
            "SECURITY_DENIED",
            False,
            "Sandbox peer authentication rejected",
            combined,
        )

    # ---------------------------------------------------------
    # Timeout
    # ---------------------------------------------------------
    lowered = combined.lower()

    if "timed out" in lowered or "timeout" in lowered:
        return build(
            operation,
            "TIMEOUT",
            False,
            "Execution timed out",
            combined,
        )

    # ---------------------------------------------------------
    # Python tests
    # ---------------------------------------------------------
    if operation == "python_test":

        # Successful unittest run
        if exit_code == 0 and "OK" in output:
            return build(
                operation,
                "PASS",
                True,
                None,
                combined,
            )

        # No tests discovered
        if exit_code == 5 and (
            "Ran 0 tests" in output
            or "NO TESTS RAN" in output.upper()
        ):
            return build(
                operation,
                "NO_TESTS",
                False,
                "No tests were discovered",
                combined,
            )

        # Test failures
        if (
            exit_code != 0
            and (
                "FAILED" in output
                or "failures=" in output
                or "errors=" in output
                or "AssertionError" in output
            )
        ):
            return build(
                operation,
                "TEST_FAILURE",
                False,
                "One or more tests failed",
                combined,
            )

        # Anything else from the test runner
        return build(
            operation,
            "RUNTIME_ERROR",
            False,
            f"Unexpected python_test result, exit_code={exit_code}",
            combined,
        )

    # ---------------------------------------------------------
    # Python compilation
    # ---------------------------------------------------------
    if operation == "python_compile":

        if exit_code == 0 and "COMPILE_OK:" in output:
            return build(
                operation,
                "PASS",
                True,
                None,
                combined,
            )

        return build(
            operation,
            "COMPILE_FAILURE",
            False,
            "Python compilation failed",
            combined,
        )

    # ---------------------------------------------------------
    # Generic operation
    # ---------------------------------------------------------
    if exit_code == 0:
        return build(
            operation,
            "SUCCESS",
            True,
            None,
            combined,
        )

    return build(
        operation,
        "RUNTIME_ERROR",
        False,
        f"Command exited with code {exit_code}",
        combined,
    )


def main():
    try:
        data = json.load(sys.stdin)
        result = normalize(data)
        print(json.dumps(result, indent=2))

    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "operation": "unknown",
                    "status": "RUNTIME_ERROR",
                    "success": False,
                    "reason": f"Normalizer error: {exc}",
                    "output": "",
                },
                indent=2,
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
