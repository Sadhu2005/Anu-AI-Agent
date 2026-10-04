# ANU: Lessons Learned

This document records real implementation bugs encountered while building ANU
and the engineering lessons they produced.

The purpose is not to hide failures. These failures helped identify boundaries
between components that were not yet explicit enough.

---

## 1. `goal_id` Type Mismatch

**Version:** V0.5.9

### What happened

The production SQLite task data used an integer-style `goal_id`, while the
`AgentGoal` model required `goal_id` to be a non-empty string.

The task could therefore exist correctly in the database but fail when it
crossed the Task → AgentGoal boundary. The mismatch was effectively:

```text
Database
goal_id = 1
```

versus:

```text
AgentGoal
goal_id = "goal-101"
```

The problem became visible when a real database task was converted into an
agent goal. Every runtime cycle logged:

```text
Scheduler cycle failed: ValueError: task cannot become AgentGoal: goal_id is missing
```

### What we changed

The task adapter (`bin/anu-agent-task-adapter-v0.1.py`) now normalises integer
database IDs to strings while preserving existing string IDs. Booleans are not
treated as integers. Missing goal IDs continue to be rejected.

Regression: `tests/test_agent_task_adapter_goal_id_v0_5_9.py`.

### Lesson

> **A database schema and an application model must have an explicit type
> contract at their boundary.**

It is not enough for each component to be internally correct. The integration
contract must specify:

```text
database representation
        ↓
adapter normalisation
        ↓
agent model representation
```

### Future rule

Whenever data crosses a component boundary, define and test:

```text
type
required / optional
valid values
normalisation
failure behaviour
```

---

## 2. Changes File Resolved From `/srv/anu/home`

**Version:** V0.5.9

### What happened

Task 186 stored this execution descriptor:

```text
project:       sandbox-test-project
changes_file:  .anu-v0.5.9-changes.json
```

The production service runs with:

```text
WorkingDirectory=/srv/anu/home
```

Safe Mutation originally loaded the changes file directly:

```python
load_changes(args.changes_file)
```

so the relative path was interpreted from the service working directory
instead of from the selected project. The intended file actually existed at:

```text
/srv/anu/workspace/projects/sandbox-test-project/.anu-v0.5.9-changes.json
```

The task consequently reached:

```text
planning → ready → running → verifying → blocked
```

without creating a mutation transaction or checkpoint.

### What we changed

The Safe Mutation boundary now resolves the project first and then resolves the
changes file inside that project:

```python
project = resolve_project(args.project)
changes_path = resolve_file(project, args.changes_file)
changes = load_changes(changes_path)
```

The existing path validation rules still apply: absolute paths and `..`
traversal are rejected, and after symlinks are resolved the final path must
still lie inside the project.

Regression: `tests/test_safe_mutation_changes_path_v0_5_9.py`.

### Lesson

> **Relative paths must be resolved relative to the resource they describe, not
> relative to an arbitrary process working directory.**

For ANU:

```text
project + relative file
        ↓
confined project path
```

is a safer contract than:

```text
relative file
        ↓
current process directory
```

### Future rule

Any execution descriptor containing a path must explicitly define:

```text
path namespace
base directory
allowed boundary
absolute-path policy
traversal policy
```

---

## 3. systemd Blocked Checkpoint Writes

**Version:** V0.5.9

### What happened

Safe Mutation correctly required checkpoints, but the production
`anu-agent.service` runs under strong systemd filesystem isolation:

```text
ProtectSystem=strict
```

with an explicit set of writable paths. The checkpoint directory
`/srv/anu/checkpoints` was not initially included in the service's writable
namespace.

So the application could logically attempt to create a checkpoint while the
operating-system boundary prevented the write.

The problem was not in the checkpoint algorithm itself. It was a mismatch
between:

```text
application capability
```

and:

```text
operating-system capability
```

### What we changed

`/srv/anu/checkpoints` was explicitly added to the service's writable paths
through the V0.5.9 systemd drop-in. A namespace probe then confirmed that the
service execution context could write to the checkpoint mutation directory.

The path was later also added to the writable list in `config/anu-policy.yaml`
so that policy and service agree.

### Lesson

> **Application-level permissions are meaningless if the operating-system
> sandbox does not provide the corresponding capability.**

ANU has multiple security layers:

```text
ANU policy
    ↓
application validation
    ↓
service sandbox
    ↓
filesystem
```

All layers must agree.

### Future rule

For every new persistent directory or capability:

```text
1. declare it in the application policy
2. declare it in the service sandbox
3. verify actual access
4. add a regression / probe
```

---

## 4. Configuration Drift

**Version:** found and aligned in V0.5.9

### What happened

As ANU grew, safety and resource rules appeared in several places. We
identified differences involving:

```text
retry limits
temperature limits
writable checkpoint paths
Docker capability wording
```

A concrete example:

```text
anu-policy.yaml
    max_retries_per_task: 5

Task Engine
    MAX_ATTEMPTS = 3
```

Another:

```text
policy / resource configuration
    70 °C

admission behaviour
    65 °C pause
```

The checkpoint directory was also present in the deployed systemd writable
boundary without being represented consistently in the policy.

### Why it happened

ANU was built incrementally. New components introduced their own configuration
or safeguards before a single authoritative configuration contract had been
established. The Task Engine, for example, was implemented with a conservative
hard-coded limit before the higher-level policy file was consolidated.

This is a normal development pattern, but it becomes dangerous when autonomous
behaviour depends on those values.

### What we changed

All values were aligned (warning 60 °C, pause 65 °C, 50 GB free disk,
3 attempts per task), and each config file now documents which component reads
it. See [CONFIGURATION.md](CONFIGURATION.md) and
[versions/v0.5.9.md](versions/v0.5.9.md).

### Lesson

> **Multiple independently maintained safety values eventually drift.**

For an autonomous system, configuration is part of the execution architecture.

- A value that appears in documentation but is not enforced is misleading.
- A value enforced in code but absent from policy is hard to audit.

### Future rule

ANU should move toward:

```text
single authoritative policy
        ↓
validated configuration
        ↓
runtime consumers
        ↓
consistency tests
```

The system should detect contradictions **before** autonomous execution begins,
for example:

```text
POLICY CONSISTENCY

retry limit              PASS
temperature threshold    PASS
RAM limit                PASS
CPU limit                PASS
parallel task limit      PASS
checkpoint access        PASS
sandbox capability       PASS
Docker direct access     DENY

POLICY CONSISTENCY: PASS
```

Planned in [V0.6.6](versions/v0.6.6.md).

---

## 5. The Common Pattern Behind These Bugs

Although the failures looked unrelated, they exposed the same deeper problem:

```text
Component A
     ↓
boundary
     ↓
Component B
```

The individual components were often correct. The missing piece was the
**contract between them**:

```text
goal_id mismatch              → data contract problem
changes-file path             → path namespace contract problem
systemd checkpoint access     → capability contract problem
configuration drift           → policy contract problem
```

This leads to one of the most important ANU engineering principles:

> **The boundaries between components need to be tested as carefully as the
> components themselves.**

---

## 6. What These Bugs Changed in the ANU Design

| Principle | Meaning |
|---|---|
| **Explicit contracts** | Each subsystem defines what it accepts and returns. |
| **Boundary validation** | Invalid state is rejected before it reaches the next layer. |
| **Least privilege** | Code needing a capability is not enough; the service must be explicitly granted only that capability. |
| **Deterministic paths** | Execution descriptors resolve resources within controlled namespaces. |
| **Single-source policy** | Safety values are not independently redefined in multiple layers. |
| **Regression before expansion** | A real integration bug is fixed together with a regression test before the architecture moves forward. |

---

## 7. The Most Important Lesson

ANU's real bugs demonstrated something that synthetic unit tests alone could
not show: **a component can pass its own tests while the complete system still
fails.**

```text
Safe Mutation        ✅
Task Engine          ✅
Agent Controller     ✅
systemd sandbox      ✅

Yet:

Agent → Task → Safe Mutation   ❌
```

The solution is not simply more isolated unit tests. ANU needs increasing
numbers of:

```text
component tests
+ integration tests
+ production-boundary tests
+ failure / recovery tests
```

That is why the later V0.5.x milestones increasingly use real database
bindings, real service boundaries, real execution specifications, and
autonomous end-to-end tasks.

---

## 8. Engineering Philosophy Produced by These Failures

> **Assume boundaries will fail, make those failures explicit, contain them
> safely, record what happened, and turn every important failure into a
> regression test or a stronger contract.**

The goal is not to build a system that never encounters errors. The goal is to
build a system where an error becomes:

```text
detected
   ↓
contained
   ↓
understood
   ↓
recorded
   ↓
recoverable
   ↓
prevented from recurring
```

That is how ANU should become more reliable as autonomy increases.

---

## Further Observations

Smaller lessons from the same period that are worth keeping.

### A. The real gate may not be the one you think

The runtime schedules work only when the Guardian reports `NORMAL`, so new
autonomous work actually stops at the **warning** temperature, not the pause
temperature. The effective behaviour of a value is defined by how it is
*used*, not by its name. *(Decision-path contract.)*

### B. Blocked means blocked, even after the fix

After the changes-file fix, task 186 does not run again by itself: the Task
Engine treats `blocked` as terminal and never auto-retries it
([ADR-009](DECISIONS.md)). Safety rules also apply to your own recovery; after
fixing an environmental cause, the task must be made runnable deliberately.
*(Lifecycle contract.)*

### C. Fast progress builds structural debt

V0.5.2 → V0.5.8 took about 31 hours. Along the way, several versions of the
same component accumulated side by side in `bin/` with about 30 backup copies,
some tests began writing to the live brain database, and the service ran code
edited in place. An agent trusted to change code needs its **own** code to be
traceable, tested in isolation, and deployed from a known commit.
*(Development contract.)*

---

## Adding a Lesson

For each new real failure, record: **What happened** (with evidence),
**What we changed** (with the regression test), **Lesson** (one sentence), and
**Future rule**. Then add it to the pattern table in section 5 if it reveals a
new kind of boundary.
