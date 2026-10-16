# ANU Testing

## Conventions

- Tests live in `tests/` (on the Pi: `/srv/anu/tests`, readable only by `anu-agent`).
- One or more test files per version step, named
  `test_<feature>_v<major>_<minor>_<patch>.py`.
- Each file is a **standalone script**: it exits `0` on success and non-zero
  on failure. There is no pytest dependency.
- A version is frozen only after its own tests **and** all earlier suites pass.

## Running

On the Pi, as the agent user:

```bash
# one test
sudo -u anu-agent python3 -B /srv/anu/tests/test_waiting_rewake_v0_5_7.py

# V0.5.1 – V0.5.2 regression runner
sudo -u anu-agent python3 -B /srv/anu/tests/run_v0_5_1_regression.py

# every test file, stop at first failure
# (tests/ is readable only by anu-agent, so the whole loop runs as that user)
sudo -u anu-agent bash -c '
  cd /srv/anu/tests
  for t in test_*.py; do
    python3 -B "$t" > /dev/null 2>&1 && echo "PASS $t" || { echo "FAIL $t"; exit 1; }
  done
'
```

`-B` prevents `__pycache__` files from being written.

## Suites by Version

| Version | Test files |
|---|---|
| V0.5.1 | `test_execution_context`, `test_task_execution_context_e2e`, `test_task_retry`, `test_functional_failure_rollback` |
| V0.5.2 | `test_retry_exhaustion`, `test_checkpoint_corruption`, `test_multi_file_mutation`, `test_concurrent_contention` |
| V0.5.3 | `test_task_graph`, `test_task_engine_dependency_admission` |
| V0.5.4 | 15 files: dependency wake, execution spec, scheduler discovery / wake plan / dispatch, production cycle; plus `test_scheduler_lifecycle`, `test_scheduler_lifecycle_integration` |
| V0.5.5 | `test_agent_model`, `test_agent_state_machine`, `test_agent_controller` |
| V0.5.6 | `test_agent_task_adapter`, `test_agent_task_bridge`, `test_scheduler_agent_integration`, `test_agent_task_execution_adapter`, `test_agent_task_execution_runtime` |
| V0.5.7 | `test_agent_waiting_lifecycle`, `test_waiting_rewake` |
| V0.5.8 | `test_agent_wake_binding`, `test_real_agent_wake_chain` |
| V0.5.9 | `test_runtime_scheduler_tick`, `test_agent_task_adapter_goal_id`, `test_safe_mutation_changes_path` |
| V0.6.0 | 9 files: `test_graceful_stop`, `test_mutation_write_ahead`, `test_mutation_idempotency`, `test_recovery_reconciler`, `test_orphan_task_recovery`, `test_crash_mid_mutation_recovery`, `test_retry_classification`, `test_scheduler_lock_recovery`, `test_startup_recovery` |
| V0.6.1 | 6 files + `run_v0_6_1_regression.py`: admission, goal service, plan service, template planner, aggregator, recovery |

Full tree on the Pi after V0.6.0: **52** files (`run_v0_5_1_regression.py`
plus 51 `test_*.py`). `run_v0_5_1_regression.py` still covers only the
V0.5.1 and V0.5.2 files. There is no single runner for the full suite yet;
deploys iterate `test_*.py` as `anu-agent` with the service stopped.

## Isolation: Important

Tests use hard-coded `/srv/anu` paths and only run on the Pi.

- 18 test files create **temporary** databases or directories.
- 7 test files reference the **live** `brain/anu-brain.db` and create no
  temporary storage:
  `test_task_retry_v0_5_1`, `test_task_execution_context_e2e_v0_5_1`,
  `test_functional_failure_rollback_v0_5_1`, `test_retry_exhaustion_v0_5_2`,
  `test_task_graph_v0_5_3`, `test_task_graph_v0_5_4`,
  `test_dependency_wake_planner_v0_5_4`.
  Some of them also mutate a real project under `workspace/projects/`.
- `test_functional_failure_rollback_v0_5_1`, `test_task_execution_context_e2e_v0_5_1`,
  and `test_multi_file_mutation_v0_5_2` require the live fixture
  `sandbox-test-project/app.py` to contain `return (a + b)`. A real task that
  changes that line breaks them, as happened at the V0.5.9 freeze. Restore
  the fixture through a task, not by hand.

Consequences: test runs create real rows in the production brain, and running
tests while the service is active can interfere with autonomous work (shared
database and scheduler lock).

**Until tests are isolated:**

1. Back up the brain first (see [OPERATIONS.md](OPERATIONS.md)).
2. Prefer running live-database tests while `anu-agent.service` is stopped.

Planned: configurable base path and temporary brain for all tests, plus a
single full-regression runner.

## Writing a New Test

1. Name it `test_<feature>_v<version>.py`.
2. Use a temporary directory and a temporary SQLite file; never the live brain.
3. Print clear `PASS` / `FAIL` lines and exit non-zero on failure.
4. Add it to the version's document under **Tests** and to the table above.
