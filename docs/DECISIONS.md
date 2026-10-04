# ANU Architecture Decision Log

This log records ANU's foundational architectural decisions and the reasoning
behind them. Each entry states the decision, why it was made, the principle
it follows, where it is enforced, what it costs, and when it should be
revisited.

**Status values:** *Accepted* (in force) · *Superseded* (replaced by a later
decision) · *Proposed* (under discussion).

Decisions 1–5 were written by the author. Decisions 6–10 are recorded from the
code and history; their rationale should be confirmed by the author.

---

## ADR-001: SQLite as the Brain

| | |
|---|---|
| **Status** | Accepted |
| **Since** | 2 Oct 2026 (Foundation: `anu-runtime.yaml` brain path) |
| **Enforced in** | `brain/anu-brain.db`; every component connects with `sqlite3` |

**Decision:** Use SQLite as ANU's persistent brain and state store.

**Why:** ANU runs on a Raspberry Pi and does not need a large distributed
database for its current workload. SQLite provides a local, durable database
without another database server or a continuously running database service.

The database persists goals, tasks, task dependencies, execution
specifications, agent events, observations, errors, resource usage,
checkpoints, mutation transactions, test results, and recovery state.

This keeps ANU's operational state close to the runtime and makes the system
easier to back up, inspect, recover, and operate on a small machine.

> **Principle:** Use the simplest durable storage system that is sufficient for
> ANU's current scale.

SQLite is therefore not merely a convenience; it is part of the
Raspberry-Pi-first architecture.

**Trade-offs:**

- One writer at a time. Fine with one parallel task, but tests and the service
  can contend for the same file ([TESTING.md](TESTING.md)).
- WAL mode means a plain file copy is not a safe backup; use the SQLite backup
  API ([OPERATIONS.md](OPERATIONS.md)).
- No schema file or migration mechanism yet; tables are created piecemeal by
  components ([DATABASE.md](DATABASE.md)).
- The brain lives only on the SD card.

**Revisit when:** ANU needs several concurrent writers, more than one machine,
or the database becomes too large to back up quickly.

---

## ADR-002: Scheduler Inside the Runtime, Not a Separate Daemon

| | |
|---|---|
| **Status** | Accepted |
| **Since** | V0.5.9 (4 Oct 2026) |
| **Enforced in** | `bin/anu-runtime.py`: `run_agent_scheduler_cycle()` called in the main loop after the Guardian check |

**Decision:** Integrate scheduler execution into the long-running
`anu-runtime.py` service instead of creating another scheduler daemon or timer.

**Why:** The real production launcher is already
`anu-agent.service → anu-runtime.py`. The runtime already owns the
resource-health loop through the Resource Guardian. Putting the scheduler tick
there keeps resource health above autonomous execution:

```text
Health Check → Resource Admission → Scheduler → Agent Execution
```

The scheduler therefore cannot independently start work while the machine is
under unsafe conditions. It also avoids another long-running process, another
service lifecycle, another lock boundary, and another source of operational
complexity.

> **Principle:** One controlled runtime should own the decision to perform
> autonomous work.

This suits the Pi: fewer background processes means a smaller operational
footprint.

**Trade-offs:**

- The scheduler tick runs **synchronously** inside the health loop. While a
  task executes (baseline test, mutation test, possibly rollback test: each
  up to 120 s in the sandbox), **health is not re-checked** and the 60 s cycle
  stretches. The Governor still admits each sandbox run individually.
- A scheduler exception is caught and logged so it cannot crash the runtime,
  but a hang inside a task would stall health reporting.

**Revisit when:** tasks become long-running (V0.6.8), or health must be
monitored during execution (V0.6.6).

---

## ADR-003: Safe Mutation Instead of Raw Shell

| | |
|---|---|
| **Status** | Accepted |
| **Since** | 2 Oct 2026 (Safe Mutation v0.1); Task Engine v0.3 delegates all changes to it |
| **Enforced in** | `bin/anu-safe-mutation-v0.2.py`; Task Engine docstring: *"never executes raw shell commands"* |

**Decision:** Do not allow ANU to modify projects through unrestricted shell
commands. Use the Safe Mutation boundary.

**Why:** Code modification is one of the highest-risk capabilities an
autonomous agent can have. Safe Mutation wraps every change in a controlled
transaction:

```text
Baseline → Checkpoint → Mutation → Test → Commit / Rollback
```

It validates project and file boundaries, records transaction information, and
preserves recovery information. ANU does not receive *"Here is a shell. Change
whatever you want."* Instead:

```text
Agent → Task Engine → Safe Mutation → Controlled Project Change
```

The execution environment stays separately restricted through the command
gateway and sandbox.

> **Principle:** Autonomous code modification must be bounded, observable,
> verifiable, and recoverable.

**Trade-offs:**

- Changes are exact string replacements (`old` must match exactly once) in
  **existing** files. Creating, deleting, or renaming files is not supported yet.
- Every change costs at least two sandbox test runs (baseline and after), three
  if it rolls back.
- A change is only as well verified as the project's own tests.

**Revisit when:** capabilities need file creation or structured edits (V0.6.2),
or verification must go beyond the test suite (V0.6.5).

---

## ADR-004: Models Off

| | |
|---|---|
| **Status** | Accepted |
| **Since** | 2 Oct 2026 (`anu-admission-policy.yaml`: `models.enabled: false`) |
| **Enforced in** | Admission policy; no model runtime installed |

**Decision:** Keep model execution disabled in the current ANU runtime.

**Why:** The current objective is to build and prove the autonomous execution
substrate before introducing expensive model computation. The Pi is not treated
as a machine that must continuously run a large AI model; it provides the
runtime, state, scheduling, governance, execution, sandboxing, verification,
and persistence layers.

Keeping models off provides predictable resource usage, lower memory pressure,
lower CPU usage, less thermal pressure, simpler debugging, and clearer
separation between the agent architecture and the intelligence provider. A
model can be introduced later without redesigning the safety and execution
foundations underneath it.

> **Principle:** Build the controlled agent operating system first; add heavier
> intelligence when it is justified.

**Trade-offs:**

- ANU cannot yet decide *what* to change. Changes files and execution
  specifications are authored in advance; ANU decides *when* and *whether it is
  safe* to apply them.

**Revisit when:** V0.6.1 (Goal → Plan) needs a planner. The choice of local
model, remote API, or both is recorded in [MODEL-STRATEGY.md](MODEL-STRATEGY.md)
and proposed as ADR-011.

---

## ADR-005: Raspberry Pi as a Design Constraint

| | |
|---|---|
| **Status** | Accepted |
| **Since** | Project start |
| **Enforced in** | Guardian, Governor, resource and admission policies, `max_parallel_tasks: 1` |

**Decision:** Design ANU specifically for the Raspberry Pi rather than assuming
a high-end server or GPU.

**Why:** The Raspberry Pi 5 has limited compute compared with a dedicated AI
server, so ANU is intentionally **time-rich but compute-constrained**. ANU does
not need to finish every task quickly; it needs to make safe progress over time:

```text
Work → Wait → Resume → Retry → Recover → Continue
```

instead of:

```text
Work as fast as possible → consume resources → fail
```

Resource governance is therefore a first-class architectural component. ANU
can pause when RAM becomes constrained, temperature becomes too high, disk
space becomes unsafe, another task is using the execution slot, or a resource
admission rule denies the operation.

> **Principle:** Optimise for reliable progress, not maximum instantaneous
> throughput.

The Raspberry Pi is not simply the cheapest machine available. It shapes ANU
into a system that can operate patiently, continuously, and safely with bounded
resources. See [DESIGN-PHILOSOPHY.md](DESIGN-PHILOSOPHY.md).

**Trade-offs:**

- One task at a time and conservative thermal limits (stop new work at 60 °C)
  cap throughput.
- The Pi is shared with production services, which always take priority.
- A single SD card holds all runtime state.

**Revisit when:** workloads routinely wait on resources, or ANU needs to manage
more than one machine.

---

## ADR-006: Policy Before Code

| | |
|---|---|
| **Status** | Accepted (recorded from history; rationale to confirm) |
| **Since** | 2 Oct 2026, 18:45 UTC |

**Decision:** Write the boundaries (identity, writable and forbidden paths,
disabled capabilities, resource limits) before writing any execution code.

**Evidence:** `config/anu-policy.yaml` is the first file in the project, written
before the Guardian, executor, gateway, or sandbox ([HISTORY.md](HISTORY.md)).

**Consequence:** every later component was built to fit inside an existing
boundary rather than having boundaries added afterwards.

---

## ADR-007: Allow-listed Command Gateway, No Shell

| | |
|---|---|
| **Status** | Accepted (recorded from code; rationale to confirm) |
| **Since** | Gateway v0.1.2, 2 Oct 2026 |
| **Enforced in** | `config/anu-command-gateway.yaml`: `shell: false`, `command_chaining: false`, `sudo: false`, `root_execution: false` |

**Decision:** ANU runs only named commands, each with a fixed executable, fixed
arguments, and its own resource budget, and every command passes the Governor.

**Consequence:** new abilities require an explicit, reviewed entry, which is
the seed of the capability architecture (V0.6.2).

---

## ADR-008: Docker Only Through a Root Sandbox Daemon

| | |
|---|---|
| **Status** | Accepted (recorded from code; rationale to confirm) |
| **Since** | Sandbox v0.2.0, 2 Oct 2026 |
| **Enforced in** | `config/anu-policy.yaml` (`docker.enabled: false`, `allow_docker_socket: false`); `config/anu-sandbox.yaml` |

**Decision:** ANU has no Docker access. Code runs in containers only via the
root `anu-sandboxd` service, with a pinned image, no network, read-only root
filesystem, all capabilities dropped, a non-root user, and 512 MB / 1 CPU /
120 s limits.

**Consequence:** Docker-socket access, which is effectively root on the host,
never reaches the agent. The sandbox daemon is a single, auditable bridge, and
because it runs as root it is a priority for the V0.6.9 security review.

---

## ADR-009: Bounded Retries; Blocked Is Terminal

| | |
|---|---|
| **Status** | Accepted (recorded from code; rationale from Task Engine docstring) |
| **Since** | Task Engine v0.4, 3 Oct 2026 |
| **Enforced in** | `MAX_ATTEMPTS = 3`; `TERMINAL = {"completed", "blocked"}` |

**Decision:** A task has at most 3 attempts. A retry must provide a **new**
mutation plan; a failed plan is never blindly re-run. `blocked` tasks are never
retried automatically because the failure may be environmental or
policy-related rather than a code defect.

**Consequence:** after fixing an environmental cause, a human must explicitly
make a blocked task runnable again (see LESSONS-LEARNED #6). Automatic,
classified recovery is planned for V0.6.0.

---

## ADR-010: Git Is Authoritative; Versions Freeze Only When Proven

| | |
|---|---|
| **Status** | Accepted |
| **Since** | V0.5.2, 3 Oct 2026 (first commit) |

**Decision:** From V0.5.2 onward, git is the record of what ANU is. Backup
files are historical evidence, not releases. A version is frozen only after its
exit criteria are demonstrated, with evidence written into its version
document. Documentation is written in the development workspace and pushed to
GitHub with the code.

**Consequence:** V0.5.9 stays *in progress* until a real task completes
through the production service, even though all of its code is written.

---

## ADR-011: API-First Intelligence Through a Model Gateway

| | |
|---|---|
| **Status** | Proposed (target V0.6.1); will supersede ADR-004 for API mode when accepted |
| **Since** | 5 Oct 2026 |
| **Enforced in** | not yet implemented; see [MODEL-STRATEGY.md](MODEL-STRATEGY.md) section 37 |

**Decision:** ANU gets intelligence from an API model first (reference:
OpenAI `gpt-6-luna`, configurable), reached only through a separate
`anu-model-gateway` service over a Unix socket. The agent itself stays
network-isolated. Local models come later, in their own service and slice.

**Why:** a capable local model does not fit in ANU's 3 GB agent budget on an
8 GB Pi. API inference keeps the Pi focused on state, execution,
verification, and recovery. Putting a gateway in front keeps the agent's
`PrivateNetwork=yes` boundary intact and gives one place to enforce budget,
privacy class, redaction, and response validation.

> **Principle:** the model may influence ANU's decisions, but it does not
> inherit ANU's permissions.

**Trade-offs:** introduces the first network-capable ANU component and an
external dependency; prompts leave the Pi (OpenAI abuse-monitoring logs keep
them up to 30 days by default); recurring cost, capped by ANU budgets ($1/day,
$5/month) and a provider-side limit.

**Revisit when:** a local model passes the memory, latency, quality, failure,
resource-governance, and privacy tests (V0.8–V0.9), or pricing or data
policy changes.

---

## How the Decisions Fit Together

```text
Raspberry Pi             → encourages bounded resource usage
Models Off               → keeps intelligence computation out of the critical runtime path
SQLite                   → simple local persistent state
Runtime-owned Scheduler  → resource health above autonomous execution
Safe Mutation            → a controlled transaction between agent decisions and code changes
```

Together they produce the core ANU philosophy:

> **ANU should not depend on unlimited compute, unrestricted machine access, or
> fast execution. It should make controlled progress, preserve state, verify its
> actions, recover from failure, and continue when conditions are appropriate.**

---

## Adding a Decision

Copy this block, give it the next number, and never delete an old entry; mark
it *Superseded by ADR-NNN* instead.

```markdown
## ADR-NNN: Title

| | |
|---|---|
| **Status** | Proposed / Accepted / Superseded by ADR-NNN |
| **Since** | version, date |
| **Enforced in** | file / config / code |

**Decision:** ...

**Why:** ...

> **Principle:** ...

**Trade-offs:** ...

**Revisit when:** ...
```
