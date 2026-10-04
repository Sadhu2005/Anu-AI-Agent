# ANU Vision

> **ANU is a controlled autonomous agent designed to make steady, safe,
> recoverable progress on meaningful work.**

This document describes what ANU should become after V0.7.0 and the intended
direction from V0.8 through V1.0.

It is a **vision** document, not a statement that these capabilities already
exist. For what exists today see [ARCHITECTURE.md](ARCHITECTURE.md); for the
committed plan up to V0.7.0 see [ROADMAP.md](ROADMAP.md).

---

## 1. What ANU Is Becoming

ANU is not intended to be only a chatbot, a code generator, or a collection of
scripts. It is intended to become a **persistent autonomous work system** that
can:

- understand goals,
- break them into manageable tasks,
- decide what can be done safely,
- execute controlled work,
- observe the result,
- test what it changed,
- checkpoint important mutations,
- recover from failures,
- wait when progress depends on something external,
- resume later,
- and maintain a durable record of what happened.

The key idea:

> **Give ANU a goal, not a pile of commands.**

ANU should gradually become capable of turning a high-level objective into a
sequence of governed, testable actions.

The underlying principles remain unchanged:

**Safety → Control → Recovery → Observability → Autonomy → Capability**

Capability grows only when the safety and recovery foundation can support it
(see [DESIGN-PHILOSOPHY.md](DESIGN-PHILOSOPHY.md)).

---

## 2. What ANU Should Do Day to Day After V0.7.0

After V0.7.0, the experience should begin to shift from **operating** ANU to
**assigning work** to ANU. Instead of repeatedly telling the system which
command to run, the user describes an objective:

> "Improve the portfolio project and make sure the changes are tested."

ANU turns that into an internal workflow:

```text
Goal → Plan → Tasks → Dependency evaluation → Safe execution → Observation
     → Tests → Checkpoint → Commit / rollback → Report
```

The user should not need to supervise every individual step.

---

## 3. Daily Operating Model

### 3.1 Give ANU a Goal

The primary interaction becomes goal-oriented:

```text
Improve the portfolio application's performance.
Find and fix the failing tests.
Research a technology and prepare a comparison.
Build the next feature for the project.
```

ANU turns the goal into structured work rather than immediately executing
arbitrary commands.

### 3.2 ANU Plans the Work

ANU determines what needs to be done, in what order, which tasks depend on
others, what resources are required, what actions are allowed, and where human
approval may be required.

The planner prefers **small, reversible, verifiable steps**:

```text
Goal
 ├── Task A: inspect current implementation
 ├── Task B: identify required change
 ├── Task C: implement change
 ├── Task D: run tests
 ├── Task E: verify result
 └── Task F: record outcome
```

### 3.3 ANU Works Without Constant Supervision

ANU continues across multiple scheduler cycles. The user does not need to stay
connected to the terminal; ANU keeps running on the Raspberry Pi:

```text
work → observe → continue
work → wait → wake → continue
work → fail → recover → retry
```

This is one of the central purposes of the persistent runtime.

### 3.4 ANU Knows When to Stop

Autonomy does not mean doing everything automatically. ANU stops when:

- a resource boundary is reached,
- an operation is outside policy,
- required information is missing,
- a dangerous or irreversible action is requested,
- human approval is required,
- repeated attempts are unsuccessful,
- or the system cannot establish that continuing is safe.

A safe system is comfortable saying:

```text
I cannot safely continue this task.
```

rather than guessing.

### 3.5 ANU Can Wait

Waiting is a normal state, not a failure:

```text
WAITING → dependency becomes available → WAKE → resume work
```

This lets ANU handle long-running goals without pretending everything can be
completed in one execution cycle. **Time is available to ANU**, a deliberate
design advantage of the Raspberry Pi architecture.

### 3.6 ANU Remembers What Happened

The brain provides durable context across executions: goals, tasks,
dependencies, observations, errors, resource usage, checkpoints, mutations,
tests, projects, commits, pull requests, and execution history.

The objective is not unlimited memory. It is **useful, durable operational
memory** ([DATABASE.md](DATABASE.md)).

### 3.7 ANU Explains Its Work

The user can ask **"What did you do?"** and get a meaningful answer:

```text
Goal: Improve portfolio test reliability

Completed:
- inspected test suite
- identified failing validation path
- applied one controlled change
- ran regression tests
- all tests passed

Result:      READY FOR REVIEW
Changes:     3 files modified
Tests:       18 passed
Checkpoint:  created successfully
```

ANU exposes enough information to build trust without forcing the user to
read internal logs.

---

## 4. The Day-to-Day Experience We Want

```text
USER → Goal → ANU ─┬─ Plan
                   ├─ Execute
                   ├─ Test
                   ├─ Observe
                   ├─ Recover
                   └─ Report → USER
```

and less:

```text
USER → command → command → command → fix error → command → inspect logs → repeat
```

The user progressively moves from **operator** to **supervisor**.

---

## 5. What V0.7.0 Should Represent

V0.7.0 is a transition point. The goal is not another feature but a reliable
**autonomous operating loop**:

```text
Goal → Task decomposition → Scheduler → Agent → Controlled execution
     → Observation → Verification → Recovery / completion
```

By this point ANU should have a stable foundation for autonomous task
progress. The exact feature set must be validated against the implementation
before release ([versions/v0.7.0.md](versions/v0.7.0.md)).

---

## 6. V0.8: Become More Capable

The central question:

> **Can ANU solve a meaningful multi-step problem rather than only execute
> individual controlled tasks?**

**Better planning:** higher-quality task graphs from goals.

```text
Goal
 ├── Research
 ├── Design
 │    ├── Decision A
 │    └── Decision B
 ├── Implementation
 ├── Testing
 └── Verification
```

**Better observation:** ANU inspects the results of its own work rather than
relying only on command success.

**Better recovery:** failures become structured events.

```text
failure → classify → is retry safe? → retry / wait / rollback / escalate
```

**Better project awareness:** projects are persistent entities, not isolated
tasks.

---

## 7. V0.9: Become More Autonomous

Autonomy at the goal level. The user can say:

> "Work on this project until the objective is complete or you need me."

```text
V0.7   User gives tasks.
V0.8   User gives larger goals.
V0.9   User gives objectives and supervises outcomes.
```

ANU becomes better at planning multi-stage work, identifying dependencies,
maintaining long-lived goals, choosing between safe alternatives, recovering
from partial failures, scheduling future work, recording decisions, and
escalating only when necessary.

Autonomy increases **without removing governance**.

---

## 8. V1.0: A Trusted Personal Autonomous Agent

V1.0 is where ANU feels like a real autonomous system rather than an
experimental framework:

```text
USER: "Build this."
ANU:  "Understood."
      PLAN → EXECUTE → TEST → OBSERVE → RECOVER WHEN NEEDED → COMPLETE → REPORT
```

The user no longer manages the internal machinery. The user manages:

```text
Goals · Priorities · Approvals · Boundaries · Results
```

---

## 9. What "Trusted" Means

Trust does not mean ANU is always correct. **Trust means the system behaves
predictably when it is wrong.**

| Property | Meaning |
|---|---|
| Bounded execution | ANU operates only within explicit permissions |
| Reversible changes | Important mutations have checkpoints and rollback paths |
| Resource awareness | CPU, memory, temperature, disk, workspace, and concurrency limits are respected |
| Durable state | A restart does not erase the operational history of a goal |
| Failure visibility | Failures become observable records, not silent corruption |
| Human escalation | ANU knows when human judgment is required |
| Auditability | Every action can be explained after the fact |

Auditability means it is always possible to answer:

```text
What did ANU do?          What was tested?
Why did it do it?         What failed?
What changed?             What was rolled back?
                          What remains incomplete?
```

---

## 10. V1.0 Does Not Mean "Unlimited Access"

> **More intelligence must not require less control.**

V1.0 does **not** mean:

- unrestricted shell access,
- unrestricted Docker control,
- uncontrolled network access,
- arbitrary package installation,
- unrestricted filesystem writes,
- unlimited resource consumption,
- or autonomous execution of dangerous actions.

ANU becomes more capable **inside** the safety architecture, not by bypassing
it. These boundaries already exist as decisions
([DECISIONS.md](DECISIONS.md): ADR-003 Safe Mutation, ADR-007 allow-listed
gateway, ADR-008 Docker only through the root sandbox daemon) and must
survive every future version.

---

## 11. The Long-Term Architecture

```text
                 USER
                  │
                GOALS
                  │
               PLANNER
                  │
              TASK GRAPH
                  │
              SCHEDULER
                  │
              AGENT CORE
           ┌──────┼──────┐
        Execute Observe Recover
           └──────┼──────┘
              GOVERNANCE
           ┌──────┼──────┐
        Policy Resources Sandbox
           └──────┼──────┘
                BRAIN
                  │
             DURABLE STATE
```

Intelligence sits above the safety substrate. **The safety substrate must
remain stronger than the intelligence layer.**

---

## 12. ANU's Relationship With Models

Models may eventually become an important component of ANU. They must not
become the safety mechanism.

```text
Model → reasoning / planning → ANU control layer → policy
      → resource governance → sandbox / controlled execution
```

not:

```text
Model → unrestricted machine access
```

A better model should make ANU more capable. It should not make ANU less
controlled. Models are currently disabled
([DECISIONS.md](DECISIONS.md), ADR-004); how they will be introduced, through
an API-first model gateway from V0.6.1, is described in
[MODEL-STRATEGY.md](MODEL-STRATEGY.md).

---

## 13. The Raspberry Pi Philosophy

ANU is deliberately developed on constrained hardware, and that constraint
stays useful. The system optimizes for:

- **correctness** over speed,
- **recovery** over restart,
- **small controlled steps** over large uncontrolled operations,
- **durability** over temporary performance,
- **observability** over hidden complexity.

The Pi does not need instant results. A task that takes 5 minutes, 2 hours,
or overnight is still a success, provided the system remains safe,
recoverable, and observable ([WHY.md](WHY.md)).

---

## 14. The Ultimate User Experience

> **The user defines the outcome they want; ANU manages the safe process
> required to reach it.**

The interaction gradually moves from:

```text
"What command should I run?"
```

to:

```text
"Here is what I want accomplished."
```

and eventually:

```text
"Keep this objective moving forward within my rules."
```

ANU then determines what to do, when to do it, how to do it, whether it is
safe, whether it worked, what to do next, when to stop, and when to ask for
help.

---

## 15. The North Star

ANU's North Star is not maximum autonomy. It is:

> **Maximum useful autonomy that remains understandable, bounded,
> recoverable, and under human control.**

```text
Reliable → Observable → Recoverable → Useful → Autonomous → More capable
```

Never reverse that order.

---

## 16. Version Direction

```text
V0.7   Reliable autonomous operating loop
V0.8   Better planning, observation, and project-level capability
V0.9   Longer-lived goals and higher autonomy
V1.0   Trusted personal autonomous agent
```

Each version increases capability while preserving the guarantees of earlier
versions. **A new capability must never silently weaken an existing safety
boundary.**

---

## 17. Where Each Vision Capability Stands

How the capabilities above map to what exists and what is planned. V0.8+ items
are direction, not commitments.

| Capability | Status | Where |
|---|---|---|
| Persistent runtime, works across scheduler cycles | Exists | V0.5.9 (embedded scheduler) |
| Resource boundary stops new work | Exists | Resource Guardian |
| Checkpoint → test → commit / rollback | Exists | Safe Mutation v0.2 |
| WAITING → wake → resume | Exists | V0.5.7 |
| Bounded retries, blocked is terminal | Exists | Task Engine (`MAX_ATTEMPTS=3`) |
| Durable state in the brain | Exists (partial) | SQLite brain |
| Crash / restart recovery, idempotent mutation | Planned | V0.6.0, V0.6.8 |
| Goal → plan → task graph | Planned | V0.6.1 |
| Policy-checked capabilities | Planned | V0.6.2 |
| Observation as memory | Planned | V0.6.3 |
| Project awareness | Planned | V0.6.4 |
| Structured failure decisions (accept / retry / rollback) | Planned | V0.6.5 |
| Human approval boundary | Planned | V0.6.7 |
| "What did you do?" reports | **Not yet assigned** | candidate for V0.6.3 or V0.7.0 |
| Model gateway (API-first, optional for planning) | Proposed | V0.6.1 ([MODEL-STRATEGY.md](MODEL-STRATEGY.md), ADR-011) |
| Model routing, local models | Direction | V0.8–V0.9 |
| Objective-level autonomy | Direction | V0.9 |
| Trusted personal agent | Direction | V1.0 |

---

## 18. Final Definition of Success

ANU succeeds when the user can give it a meaningful objective and reasonably
expect:

```text
ANU will work on it.
ANU will work safely.
ANU will remember its progress.
ANU will verify its changes.
ANU will recover when possible.
ANU will stop when it must.
ANU will ask for help when necessary.
ANU will clearly explain the result.
```

That is the direction from V0.7.0 to V1.0.

**ANU is being built to become a persistent, governed, recoverable autonomous
worker, not merely a smarter command executor.**
