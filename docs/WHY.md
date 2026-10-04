# Why ANU Is Built This Way

## The Goal

ANU was never meant to be a chatbot that can call tools.

The goal is a system that can **autonomously take actions on a real machine
without becoming dangerous or unreliable**.

That single requirement changes the order in which everything has to be built.

## Chatbot vs Autonomous Actor

**A tool-calling chatbot can afford to be wrong.** It suggests something, a
human reads it, and nothing happens until the human acts. The human is the
safety layer, so safety can be added later.

**ANU takes actions itself**, on a real Raspberry Pi, without a human in the
loop. Every mistake becomes a real change to a file, a project, or a running
service. In that world:

- intelligence without **safe** execution is dangerous
- intelligence without **reliable** execution is unpredictable
- so execution must be trustworthy **before** anything smart is put on top of it

## The Build Order

Most agent projects are built top-down: start with "decide what to do", then
discover the "do it" layer cannot be trusted. ANU is built bottom-up:

```text
1. Can it act safely?          checkpoints, rollback, sandbox, allow-list    V0.5.1 – V0.5.3
2. Can it act on its own?      scheduler → agent → production service        V0.5.4 – V0.5.9
3. Can it act reliably?        retry, recovery, idempotency                  V0.6.0
4. Can it decide what to do?   goal → plan → task graph                      V0.6.1
5. Can it use tools safely?    policy-checked, audited capabilities          V0.6.2
6. Can it learn?               observation → memory                          V0.6.3 – V0.6.4
7. Can it judge its own work?  verification, approval, resource awareness    V0.6.5 – V0.6.7
8. Can it run indefinitely?    durable state, recovery, hardening            V0.6.8 – V0.6.9
9. One coherent agent          goal → plan → execute → verify → remember     V0.7.0
```

> **Rule:** do not jump straight to an "AI brain". A smart planner on top of an
> unreliable execution substrate is the most dangerous combination possible.

## Design Principles

These follow directly from the goal and are enforced in the code.

1. **No direct action on projects.** The Task Engine never runs raw shell
   commands against a project; every change goes through Safe Mutation.
2. **Every change is reversible.** Checkpoint first, apply, verify, then commit
   or roll back.
3. **Only named, allow-listed commands.** The command gateway accepts known
   operations (e.g. `python_test`, `git_status`), not arbitrary shell.
4. **Isolation by default.** ANU has no direct Docker access. Test and compile
   run in Docker containers with no network, but only through the root
   `anu-sandboxd` service.
5. **The machine comes first.** The Resource Guardian sits *above* the
   scheduler: if the Pi is not `NORMAL`, ANU does not act. Production services
   on the Pi always have priority over ANU.
6. **Failure is classified, not repeated.** A retry needs a new mutation plan;
   `blocked` tasks are never retried automatically, because the cause may be
   environmental or policy-related.
7. **Waiting is a real state.** Not everything finishes immediately; a task can
   wait for a condition and be woken later.
8. **Proven, not written.** A version is frozen only when its behaviour has been
   demonstrated (e.g. V0.5.9 is not frozen until a real task completes through
   the production service).
9. **Autonomous when safe, human when risky, denied when unsafe.**

## Applying the Same Rule to ANU Itself

If ANU must never become dangerous or unreliable on a real machine, the way
ANU *itself* is built and deployed must meet the same standard it demands of
every project it touches:

| ANU requires of projects | ANU must also require of itself |
|---|---|
| Changes go through a controlled path | ANU's own code is deployed from git, not edited in place on the Pi |
| Every change is traceable | The running service always corresponds to a known commit or tag |
| Verification before acceptance | Tests run in isolation, never against the live brain database |
| One clear policy | One authoritative resource policy, not several that disagree |

An agent that is trusted to change code needs its own code to be fixed,
traceable, and verifiable.

## Further Reading

- [DESIGN-PHILOSOPHY.md](DESIGN-PHILOSOPHY.md): why ANU is time-rich but compute-constrained
- [ARCHITECTURE.md](ARCHITECTURE.md): how the execution chain works today
- [ROADMAP.md](ROADMAP.md): the plan from V0.5.9 to V0.7.0
- [versions/](versions/): what each version built and how it was proven
