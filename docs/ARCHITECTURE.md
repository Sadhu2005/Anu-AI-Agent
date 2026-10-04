# ANU Architecture

## Current Execution Chain (V0.5.9)

ANU is no longer a collection of components: the components are connected into
a real autonomous execution loop, driven by the production service itself.

```text
anu-agent.service (systemd)
        │
        ▼
/srv/anu/bin/anu-runtime.py          long-lived runtime, 60 s loop
        │
        ▼
Resource Guardian                    anu-resource-guardian.py
        │
  status == NORMAL ?
   ┌────┴────┐
  YES        NO ──► skip cycle, log "Scheduler skipped"
   │
   ▼
Task Scheduler Lifecycle             anu-task-scheduler-v0.1.py
   │                                 run_agent_scheduler_cycle_once()
   ▼
Agent-aware WAKE handler             build_production_agent_wake_execution_handler()
   │
   ▼
Agent Task Execution Runtime         anu-agent-task-execution-runtime-v0.1.py
   │
   ▼
Task Adapter → AgentGoal             anu-agent-task-adapter-v0.1.py
   │
   ▼
Agent Controller                     anu-agent-controller-v0.1.py
   │
   ▼
Task Engine                          anu-task-engine-v0.5.3.py
   │
   ▼
Execution Specification              anu-task-execution-spec-v0.1.py
   │
   ▼
Safe Mutation v0.2                   anu-safe-mutation-v0.2.py
   │
   ├──► Checkpoint system            /srv/anu/checkpoints/mutations
   │
   ▼
Command Gateway → Executor → Sandbox anu-command-gateway.py / anu-executor.py / anu-sandboxd.py
   │
   ▼
Tests / run  →  Commit or Rollback
   │
   ▼
SQLite Brain + event logs            brain/anu-brain.db, logs/*.jsonl
   │
   ▼
COMPLETE / FAILED / BLOCKED
   │
   ▼
WAITING  ──►  re-wake later
```

## Components

| Layer | File | Responsibility |
|---|---|---|
| Service | `anu-agent.service` | systemd unit; restricts writable paths (includes `/srv/anu/checkpoints`) |
| Runtime | `bin/anu-runtime.py` | Health loop; runs one scheduler tick per cycle when health is NORMAL |
| Guardian | `bin/anu-resource-guardian.py` | Measures RAM, disk, CPU, temperature; returns NORMAL / non-NORMAL |
| Governor | `bin/anu-resource-governor.py` | Enforces ANU resource budgets and admission policy |
| Scheduler | `bin/anu-task-scheduler-v0.1.py` | Discovers READY tasks, builds WAKE plans, dispatches bounded cycles under a lock |
| Task graph | `bin/anu-task-graph-v0.1.py` | Dependencies between tasks; READY vs WAITING admission |
| Execution spec | `bin/anu-task-execution-spec-v0.1.py` | Persisted descriptor per task (project, changes file, operation) |
| Agent model | `bin/anu-agent-model-v0.1.py` | `AgentGoal`, `AgentAction`, `AgentPlan`, `AgentResult` |
| Agent runtime | `bin/anu-agent-runtime-v0.1.py` | Agent state machine (NEW → … → COMPLETE / FAILED) |
| Agent controller | `bin/anu-agent-controller-v0.1.py` | Drives a goal through the state machine |
| Adapters | `bin/anu-agent-task-adapter-v0.1.py`, `-bridge-`, `-execution-adapter-`, `-execution-runtime-` | Convert tasks ↔ agent goals and carry results back |
| Task engine | `bin/anu-task-engine-v0.5.3.py` | Task lifecycle, attempts (max 3), dependency admission; delegates changes to Safe Mutation |
| Safe mutation | `bin/anu-safe-mutation-v0.2.py` | Checkpoint → apply changes → verify → commit or rollback |
| Gateway | `bin/anu-command-gateway.py` | Allow-listed commands only (e.g. `python_test`, `git_status`) |
| Executor | `bin/anu-executor.py` | Runs commands under an execution profile (RAM, timeout, output limits) |
| Sandbox | `bin/anu-sandboxd.py`, `bin/anu-sandbox-client.py` | Root daemon; the only path from ANU to Docker-isolated execution (`python-sandbox` profile) |

## Agent States

```text
NEW → READY → PLANNING → EXECUTING → OBSERVING → COMPLETE
                                          │
                                          ├──► RETRY → EXECUTING
                                          ├──► FAILED
                                          └──► WAITING ──(event)──► wake ──► continue
```

WAITING is not terminal (since V0.5.7).

## Task Engine Statuses

`pending`, `ready` (runnable) → `planning` → `running` → `verifying` →
`completed` | `failed` (retryable, max 3 attempts) | `blocked` (terminal, not
auto-retried because the cause may be environmental or policy-related).

## Resource Policy

Why the limits are conservative: see [DESIGN-PHILOSOPHY.md](DESIGN-PHILOSOPHY.md).

Three files hold resource settings. Since V0.5.9 they are aligned, with
`config/anu-admission-policy.yaml` as the reference values:

| File | Read by | Role |
|---|---|---|
| `config/anu-resources.yaml` | `bin/anu-resource-guardian.py` | Host health status (NORMAL / WARNING / PAUSE) that gates the scheduler |
| `config/anu-admission-policy.yaml` | `bin/anu-resource-governor.py` | Per-workload admission (RUN / PAUSE / DENY / APPROVAL) |
| `config/anu-policy.yaml` | not read by code yet | Declarative identity, filesystem, capability, and limits policy |

| Setting | Value | Where |
|---|---|---|
| Warning temperature (new work stops) | 60 °C | resources, admission |
| Pause temperature | 65 °C | resources, admission, policy |
| Resume temperature | 55 °C | resources (not yet used by code; hysteresis planned in V0.6.6) |
| Minimum free disk | 50 GB | resources, admission |
| Guardian emergency free RAM (PAUSE) | 1024 MB | resources |
| Governor host RAM reserve | 2048 MB | admission |
| ANU RAM / CPU budget | 3072 MB / 50 % | all three |
| Parallel tasks | 1 | all three |
| Max attempts per task | 3 | Task Engine `MAX_ATTEMPTS`, policy |
| Workspace / project max | 30 GB / 15 GB | admission |
| File size: normal / approval / absolute | 250 / 2048 / 2048 MB | admission |

The runtime only schedules while the Guardian reports `NORMAL`, so in practice
**new autonomous work stops at the warning temperature (60 °C)**, and running
workloads pause at 65 °C.

## Docker and the Sandbox

`docker.enabled: false` in `config/anu-policy.yaml` means **ANU cannot use
Docker directly**: it has no Docker socket access and cannot start containers.
Code is still executed inside Docker, but **only** through the root
`anu-sandboxd` service, via the `python-sandbox` execution profile (no network,
512 MB RAM, 120 s timeout). The sandbox daemon is the single, controlled bridge
between ANU and Docker.

## Target Architecture (V0.7.0)

```text
GOAL → PLANNER → TASK GRAPH → SCHEDULER → CONTROLLER → TOOLS
     → EXECUTION → OBSERVATION → VERIFICATION → COMPLETE | RETRY
     → MEMORY → NEXT GOAL
```

Underneath everything:

```text
Resource Governor · Security Policy · Checkpoints · Database
Audit Log · Recovery · Human Approval
```
