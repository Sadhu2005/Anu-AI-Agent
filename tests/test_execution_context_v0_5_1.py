#!/usr/bin/env python3

import importlib.util
import os
import sys
from pathlib import Path


SAFE_MUTATION = Path("/srv/anu/bin/anu-safe-mutation-v0.2.py")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_single_execution_context_definition():
    source = SAFE_MUTATION.read_text(encoding="utf-8")
    count = source.count("def execution_context(")

    assert count == 1, (
        f"Expected exactly one execution_context() definition, found {count}"
    )


def test_execution_context_contract():
    module = load_module(SAFE_MUTATION, "anu_safe_mutation_test")

    os.environ["ANU_TASK_ID"] = "7"
    os.environ["ANU_RUN_ID"] = "regression-run-001"
    os.environ["ANU_ATTEMPT"] = "99"

    context = module.execution_context()

    assert isinstance(context, tuple)
    assert len(context) == 5

    task_id, goal_id, parent_task_id, run_id, attempt = context

    assert task_id == 7
    assert run_id == "regression-run-001"
    assert attempt == 99

    print("PASS execution_context contract")
    print(f"  task_id={task_id}")
    print(f"  goal_id={goal_id}")
    print(f"  parent_task_id={parent_task_id}")
    print(f"  run_id={run_id}")
    print(f"  attempt={attempt}")


def test_compilation():
    source = SAFE_MUTATION.read_text(encoding="utf-8")

    compile(
        source,
        str(SAFE_MUTATION),
        "exec",
    )

    print("PASS safe mutation syntax")


def main():
    tests = [
        test_single_execution_context_definition,
        test_execution_context_contract,
        test_compilation,
    ]

    failures = 0

    for test in tests:
        try:
            test()
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}", file=sys.stderr)

    if failures:
        print(f"REGRESSION_TEST_FAILED count={failures}")
        return 1

    print("REGRESSION_TEST_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
