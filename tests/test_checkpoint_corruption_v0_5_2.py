#!/usr/bin/env python3

import hashlib
import importlib.util
import os
import tempfile
from pathlib import Path


SAFE_MUTATION = Path("/srv/anu/bin/anu-safe-mutation-v0.2.py")


def load_safe_mutation():
    spec = importlib.util.spec_from_file_location(
        "anu_safe_mutation",
        SAFE_MUTATION,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Safe Mutation Engine")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def main():
    safe = load_safe_mutation()

    with tempfile.TemporaryDirectory(prefix="anu-checkpoint-test-") as td:
        root = Path(td)
        project = root / "project"
        checkpoint_dir = root / "checkpoint"

        project.mkdir()
        checkpoint_dir.mkdir()

        target = project / "app.py"
        checkpoint = checkpoint_dir / "app.py"

        original = b"def add(a, b):\n    return a + b\n"
        corrupted = b"THIS CHECKPOINT IS CORRUPTED\n"

        target.write_bytes(original)
        checkpoint.write_bytes(corrupted)

        expected_sha256 = sha256_bytes(original)

        item = {
            "path": "app.py",
            "checkpoint": str(checkpoint),
            "baseline_sha256": expected_sha256,
            "mode": target.stat().st_mode,
        }

        # ---------------------------------------------------------
        # Corrupted checkpoint must be rejected BEFORE overwrite.
        # ---------------------------------------------------------
        before = target.read_bytes()

        try:
            safe.rollback_files([item], project)
        except RuntimeError as exc:
            message = str(exc)
            assert "Checkpoint integrity mismatch" in message
            print("PASS corrupted checkpoint rejected")
        else:
            raise AssertionError(
                "Corrupted checkpoint was accepted"
            )

        assert target.read_bytes() == before
        print("PASS project unchanged after rejected rollback")

        # ---------------------------------------------------------
        # Valid checkpoint must still restore normally.
        # ---------------------------------------------------------
        checkpoint.write_bytes(original)

        safe.rollback_files([item], project)

        assert target.read_bytes() == original
        print("PASS valid checkpoint rollback")

        # ---------------------------------------------------------
        # Missing checkpoint must still be rejected.
        # ---------------------------------------------------------
        checkpoint.unlink()

        try:
            safe.rollback_files([item], project)
        except RuntimeError as exc:
            assert "Missing checkpoint" in str(exc)
            print("PASS missing checkpoint rejected")
        else:
            raise AssertionError(
                "Missing checkpoint was accepted"
            )

    print()
    print("CHECKPOINT_CORRUPTION_TEST_PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"CHECKPOINT_CORRUPTION_TEST_FAILED: {exc}",
            file=os.sys.stderr,
        )
        raise SystemExit(1)
