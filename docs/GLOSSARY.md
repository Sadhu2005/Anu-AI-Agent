# ANU Glossary

| Term | Meaning |
|---|---|
| **Admission** | Decision whether a task or workload may start. *Dependency admission*: all prerequisite tasks are complete (task graph). *Resource admission*: the Governor allows the workload's RAM, disk, and temperature cost. |
| **Agent** | The goal-driven layer (`AgentGoal`, `AgentPlan`, `AgentAction`, `AgentResult`) above the Task Engine, driven by the AgentController. |
| **Agent states** | `NEW`, `READY`, `PLANNING`, `EXECUTING`, `OBSERVING`, `WAITING`, `RETRY`, `COMPLETE`, `FAILED`. |
| **Attempt** | One execution of a task. A task has at most 3 attempts (`MAX_ATTEMPTS`). |
| **Baseline test** | The test run *before* any change. If it fails, Safe Mutation aborts (`ABORTED_BASELINE_FAILURE`) because a broken project cannot prove a change is good. |
| **Blocked** | Terminal task status. Never retried automatically because the cause may be environmental or policy-related. |
| **Brain** | The SQLite database `brain/anu-brain.db`: tasks, goals, events, transactions, resource history. |
| **Changes file** | JSON array of `{"file", "old", "new"}` replacements. Each `old` must match exactly once. Resolved inside the project. |
| **Checkpoint** | Copy of each file plus its SHA-256 and a `manifest.json`, stored under `checkpoints/mutations/<project>/<transaction_id>/` before a change. |
| **Command gateway** | `anu-command-gateway.py`. Runs only named, allow-listed commands; no shell, no chaining, no sudo. |
| **Committed** | Safe Mutation outcome: the change was applied and the test passed. |
| **Execution profile** | Resource envelope for a command: RAM, storage, timeout, output size, network, filesystem mode, sandbox requirement. |
| **Execution specification** (descriptor) | Per-task record of *how* to execute it: `project`, `changes_file`, `operation`. Table `task_execution_specs`. |
| **Freeze** | Declaring a version complete after its exit criteria are *demonstrated*. Recorded by a commit and ideally a tag. |
| **Governor** | `anu-resource-governor.py`. Per-workload decision: ALLOW, PAUSE, DENY, APPROVAL, PREFLIGHT. Reads `anu-admission-policy.yaml`. |
| **Guardian** | `anu-resource-guardian.py`. Host health status: `NORMAL`, `WARNING`, `PAUSE`. Reads `anu-resources.yaml`. The runtime schedules work only on `NORMAL`. |
| **Operation** | A named sandbox action such as `python_test` (unittest discovery) or `python_compile`. |
| **Rolled back** | Safe Mutation outcome: the test failed after the change, files were restored from the checkpoint, hashes matched the baseline, and the test passed again. |
| **Runtime** | `anu-runtime.py`, the long-lived process started by `anu-agent.service`. |
| **Sandbox** | Docker container run by the root `anu-sandboxd` daemon: no network, read-only root, all capabilities dropped, 512 MB, 1 CPU, 120 s. ANU has no direct Docker access. |
| **Scheduler tick / cycle** | One pass of the scheduler: discover READY tasks, build WAKE plan, dispatch at most `max_tasks` (1). Protected by `runtime/anu-scheduler.lock`. |
| **Task** | A unit of work in the `tasks` table with a status and attempt count. |
| **Task graph** | Dependencies between tasks (`task_dependencies`), with cycle prevention. |
| **Task statuses** | `pending`, `ready`, `planning`, `running`, `verifying`, `completed`, `failed`, `blocked`. |
| **Transaction** | One Safe Mutation run, identified by `mut-<timestamp>-<id>` and stored in `mutation_transactions`. |
| **WAKE** | Scheduler decision that a READY task should be started now. |
| **WAITING** | A task or goal that is not finished but cannot proceed yet. Not terminal; it is re-woken when its condition is met. |
