# ANU History

## In One Paragraph

ANU started as a **controlled execution and sandboxing project**, evolved into a
**dependency-aware task engine and scheduler**, then gained an **agent
controller and a persistent WAITING / re-wake lifecycle**, and is now being
connected into a **production autonomous runtime** where the agent can safely
execute code-changing tasks under resource, filesystem, sandbox, checkpoint,
rollback, and verification boundaries.

## How to Read This Document

The history has two layers:

1. **Pre-git / early prototype**: 2 October 2026 to the morning of 3 October 2026
2. **Canonical git history**: from V0.5.2 (commit `067e58d`, 3 October 2026) onward

> **Rule:** git is authoritative for committed versions. Older backup files are
> historical evidence, not official releases.
>
> ```text
> backup filename  ≠  official git release
> ```

Every statement below is marked:

| Mark | Meaning |
|---|---|
| **Verified** | Confirmed from file contents, file timestamps, or git |
| **Inferred** | Reasonable reading of filenames or code, not proven |

Timestamps are file modification times in **UTC**, read from `/srv/anu` on the
Pi. A backup copy's timestamp may be the time it was copied rather than the
time its contents were written.

---

## Before ANU: Preparing the Pi

| When | Event | Evidence |
|---|---|---|
| ~15 Sep 2026 | Operator account `anu-vps1947` created on the Raspberry Pi 5 (Debian 13) | Verified: home directory dates |
| 26–27 Sep 2026 | Docker installed and tested (`hello-world` containers); portfolio site stack deployed | Verified: Docker images and containers dates |

The portfolio stack (nginx, web, API, Postgres) runs on the same Pi but is
**not part of ANU**. It is why ANU's resource policy gives production services
priority.

---

## Pre-git Era: 2 October 2026

All of ANU's foundation was built in one evening, in this order.

### Phase 1: Skeleton and policy (18:42 – 18:48)

**Verified:** `/srv/anu/src` created at 18:42; the first configuration files
followed within minutes:

| Time | File |
|---|---|
| 18:45 | `config/anu-policy.yaml`: identity, filesystem boundaries, everything disabled |
| 18:46 | `config/anu-projects.yaml`: explicit project registration rules |
| 18:47 | `config/anu-resources.yaml`: resource thresholds |
| 18:48 | `config/anu-runtime.yaml`: paths for brain, logs, runtime, workspace |

**Inferred:** ANU started **policy-first**: what it may touch and what it must
never do was written before any execution code.

### Phase 2: Observe and govern the machine (19:00 – 19:57)

| Time | Component | Evidence |
|---|---|---|
| 19:00 | `anu-resource-guardian.py`: host health (CPU, RAM, temperature, disk) | Verified |
| 19:04 – 19:10 | `anu-executor.py` + `anu-executor.yaml`: bounded workspace file operations | Verified |
| 19:41 – 19:48 | `anu-admission-policy.yaml` + `anu-resource-governor.py`: per-workload ALLOW / PAUSE / DENY / APPROVAL / PREFLIGHT | Verified |
| 19:52 – 19:57 | `anu-command-gateway.yaml` + `anu-execution-profile.py`: allow-listed commands | Verified |

### Phase 3: Command gateway v0.1.x (20:00 – 21:09)

| Artefact | Time | What it contains |
|---|---|---|
| `anu-command-gateway.py.v0.1.2.backup` | 20:00 | **Verified:** 767 lines; Governor admission (PREFLIGHT, APPROVAL); **no** sandbox, **no** execution profiles |
| `anu-command-gateway.py.v0.1.3-pre-sandbox` | 21:09 | **Verified:** 553 lines; references sandbox and execution profiles, but does **not** yet route `python_test` to the sandbox |

**Inferred:** v0.1.2 was a governed, allow-listed command runner. v0.1.3 was a
restructuring toward execution profiles, saved just before Python tests were
routed into the Docker sandbox. The gateway's config file still reports
`version: "0.1.2"`.

### Phase 4: Docker sandbox daemon v0.2.0 (20:45 – 20:52)

| Artefact | Time | What it contains |
|---|---|---|
| `anu-sandboxd.py.v0.2.0-pre-user` | 20:45 | **Verified:** 404 lines; Docker execution, no explicit container user |
| `anu-sandboxd.py.v0.2.0-pre-admission` | 20:52 | **Verified:** 408 lines; adds a non-root container user (`--user`) |
| `anu-sandboxd.py` (current) | 20:52 | **Verified:** 663 lines; adds Governor admission |
| `anu-sandbox.yaml`, `anu-sandbox-client.py` | 20:52 | **Verified** |

**Inferred:** the sandbox was hardened in two explicit steps: first *who* runs
inside the container, then *whether* the host can afford it.

### Phase 5: Profiles, results, and sandboxed tests (21:06 – 21:30)

| Artefact | Time | Notes |
|---|---|---|
| `anu-execution-profiles.yaml` | 21:06 | Verified: `inspect`, `git-read`, `python-basic`, `system-info`, `python-sandbox` |
| `anu-result-normalizer.py` | 21:21 | Verified: uniform results |
| `anu-command-gateway.py.backup-20261002-222820` | 21:28 | **Verified:** 875 lines; first gateway version that routes **`python_test` to the sandbox** |

### Phase 6: Safe Mutation (21:43 – 21:52)

| Artefact | Time | What it contains |
|---|---|---|
| `anu-safe-mutation.py` (v0.1) | 21:43 | **Verified:** 304 lines; checkpoint, rollback, SHA-256 integrity, `python_test` verification |
| `anu-safe-mutation-v0.2.py.backup-v0.3.1-…` | 21:52 | **Verified:** 424 lines; adds `task_id` linkage |

**Inferred:** this is the moment ANU became able to **change code safely**:
checkpoint → change → test → commit or roll back.

### Phase 7: Task Engine v0.3 and the "v0.3.1" checkpoint (22:10 – 22:34)

| Artefact | Time | What it contains |
|---|---|---|
| `anu-task-engine-v0.3.py` | 22:10 | **Verified** docstring: *"Orchestrates task lifecycle while delegating filesystem changes and code verification to the existing Safe Mutation Engine. It never executes raw shell commands itself."* |
| `*.backup-v0.3.1-20261002-223417` (gateway 1083 lines, safe mutation) | 22:34 | **Verified:** `task_id` threaded through gateway and Safe Mutation |

**Inferred:** "v0.3.1" was a milestone where every command and mutation became
attributable to a task.

---

## Pre-git Era: 3 October 2026, Morning

### Brain snapshot

**Verified:** `brain/anu-brain.db.backup-v0.2-20261003T080022Z` taken at 08:00,
between Task Engine v0.4 and v0.5.

### Task Engine v0.4 → v0.5 → v0.5.1 (07:52 – 08:12)

| File | Time | Change (verified from docstring / code) |
|---|---|---|
| `anu-task-engine-v0.4.py` | 07:52 | Explicit retry semantics: `MAX_ATTEMPTS`, a retry needs a **new** mutation plan, `blocked` never auto-retried |
| `anu-task-engine-v0.5.py` | 08:04 | Adds `run_id` per execution |
| `anu-task-engine-v0.5.1.py` + `anu-execution-context-v0.5.1.py` | 08:12 | Task / run / attempt context propagated into gateway and Safe Mutation events |

### Safe Mutation hardening (08:13 – 08:59)

Verified backup sequence of `anu-safe-mutation-v0.2.py`:
`backup-v0.5.1-context` (08:13) → `pre-context-fix` (08:27) →
`pre-dedupe` (08:30) → `pre-checkpoint-integrity` (08:59).

**Inferred:** execution context was added, then fixed; duplicate file entries in
one transaction were rejected; and checkpoint integrity verification (SHA-256
check before restoring) was added. These became the V0.5.2 test themes
(checkpoint corruption, multi-file mutation).

### First git commit (09:21 UTC)

**Verified:** `067e58d` "Freeze ANU V0.5.2" (tag `v0.5.2`), 26 files,
9,493 lines, committed at 10:21 Pi local time (UTC+1).

From this point git is the record. (`anu-task-engine-v0.5.3.py` shows a later
timestamp, 10:13 UTC, because it was edited again for dependency admission in
V0.5.3.)

---

## Git Era: V0.5.2 Onward

Summarised in [CHANGELOG.md](CHANGELOG.md), in full detail in
[versions/](versions/).

| Date | Version | Milestone |
|---|---|---|
| 3 Oct 10:21 | V0.5.2 | Foundation frozen in git |
| 3 Oct 11:50 | V0.5.3 | Dependency graph and admission |
| 3 Oct 14:56 – 20:15 | V0.5.4 | Scheduler, wake planning, execution specification |
| 3 Oct 21:45 | V0.5.5 | Agent model, state machine, controller |
| 3 Oct 22:13 | V0.5.6 | Task ↔ agent adapters, execution results preserved |
| 3 Oct 22:41 | V0.5.7 | WAITING and re-wake |
| 4 Oct 17:24 | V0.5.8 | Agent-aware scheduler |
| 4 Oct | V0.5.9 | Production runtime runs the scheduler; first real autonomous task reaches Safe Mutation (blocked by changes-file path) |
| 5 Oct 04:56 | V0.5.9 | **First autonomous COMMITTED mutation** (task 186) and verified rollback (task 187) by the production service; 42 / 42 regression; frozen |
| 15 Oct | V0.6.0 | Graceful stop, write-ahead transactions, recovery under execution lock, interrupted-only retry; steps 1–4 deployed |
| 15–16 Oct | V0.6.0 | Live evidence: restart and reboot complete mid-mutation; kill and forced reboot recover and retry once (tasks 224–227) |
| 16 Oct | V0.6.1 | Goal → Plan → Task Graph: admission, goals/plans, template expand, aggregator, recovery; regression 6/6; live goals 6–7 |

(Git times are Pi local time, UTC+1. All other times in this document are UTC.)

## The Arc

```text
2 Oct evening   policy → health → governor → gateway → sandbox → safe mutation → task engine
3 Oct morning   retries → run/attempt context → mutation integrity → first git freeze
3 Oct day       dependency graph → scheduler → execution specification
3 Oct night     agent model → controller → adapters → WAITING / re-wake
4 Oct           agent-aware scheduler → production runtime → first autonomous task
5 Oct           first autonomous COMMITTED / ROLLED_BACK by the production service
15–16 Oct       reliable execution: recover from kill, reboot, and power cut mid-mutation
```

Controlled execution came first, then tasks, then scheduling, then the agent,
then autonomy, then reliability: the same order that [WHY.md](WHY.md) argues for.

## Version Numbering Note

Before git, version numbers belonged to **components** (gateway v0.1.x, sandbox
v0.2.0, Safe Mutation v0.2, Task Engine v0.3 – v0.5.3). From V0.5.2 onward the
number is the **project** version, while component files keep their own
numbers (for example `anu-task-scheduler-v0.1.py` in project V0.5.8).

## Open Questions

To be confirmed by the author:

- Was there any ANU code or design before 2 October 2026 (for example on
  another machine, or a design document)?
- What did gateway "v0.1.0" and "v0.1.1" contain, if they existed?
- Was "v0.3.1" a deliberate milestone name, or only a backup label?
- What was in the brain database at `backup-v0.2` (8 a.m., 3 October)?
