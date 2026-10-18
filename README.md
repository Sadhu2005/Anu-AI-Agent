# ANU

**A safety-first autonomous agent runtime that lives on a Raspberry Pi: it
changes real code only through checkpoints, tests, and rollback, and keeps
making safe progress with limited resources.**

> Time-rich, compute-constrained. ANU does not need to be fast to be useful;
> it needs to be safe, persistent, resource-aware, recoverable, and eventually
> autonomous.

## What ANU Is

ANU is not a chatbot that calls tools. It is a long-running system service
that **discovers work, decides whether the machine is healthy enough to do it,
and carries it out on its own**, with every code change checkpointed, tested in
an isolated sandbox, and either committed or rolled back.

```text
anu-agent.service
   → runtime (60 s loop)
   → Resource Guardian: is the Pi healthy?   ── no ──► wait
   → scheduler → agent → controller → task engine
   → Safe Mutation: checkpoint → change → test in sandbox
   → COMMITTED or ROLLED_BACK → SQLite brain
```

## Current Status

| | |
|---|---|
| **Version** | V0.6.7 frozen (tag `v0.6.7`); branch **`v0.6.7-integration`** |
| **Proven** | V0.5.9–V0.6.6 as before; V0.6.7 approve id=1 → task 257 pending on Pi |
| **Maturity** | V0.6.6 **100%** · V0.6.7 **100%** |
| **Next** | Long-running agent (V0.6.8) |
| **Then** | V0.6.9 hardening → V0.7.0 agent runtime |

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full plan.

## Safety Model in One Screen

- **No raw shell.** Only named, allow-listed commands through a command gateway.
- **Every change is reversible.** Baseline test → checkpoint → apply → test →
  commit, or roll back and verify the rollback.
- **Isolated execution.** Tests run in a Docker sandbox with no network,
  read-only root filesystem, all capabilities dropped, 512 MB RAM, 120 s.
  ANU itself has no Docker access; only the root `anu-sandboxd` service does.
- **The machine comes first.** No new work above 60 °C, pause at 65 °C, keep
  50 GB disk and RAM reserves free, one task at a time.
- **Failure is classified, not repeated.** Max 3 attempts; blocked tasks are
  never retried automatically.
- **No network, browser, package installs, git push, deployment, or sudo** at
  this stage. No AI model is running on the Pi yet.

## Repository Layout

```text
bin/      ANU components (runtime, guardian, scheduler, agent, task engine,
          safe mutation, gateway, executor, sandbox)
config/   policy and resource configuration (YAML)
tests/    versioned tests, one or more per version step
docs/     documentation: why, philosophy, architecture, roadmap, versions
```

Runtime state (`brain/`, `checkpoints/`, `logs/`, `runtime/`, `workspace/`,
`sandbox/`, `browser/`, `home/`, `secrets/`) lives only on the device and is
never committed.

## Platform

| | |
|---|---|
| Hardware | Raspberry Pi 5, 4 cores, 8 GB RAM |
| OS | Debian 13 (trixie), aarch64 |
| Language | Python 3.13 |
| Storage | SQLite (`brain/anu-brain.db`) + JSONL logs |
| Isolation | Docker (sandboxed operations only), systemd |
| Install path | `/srv/anu`, user `anu-agent` |

## Documentation

| Start here | |
|---|---|
| [docs/WHY.md](docs/WHY.md) | Why ANU is built safety-first |
| [docs/DESIGN-PHILOSOPHY.md](docs/DESIGN-PHILOSOPHY.md) | Time-rich, compute-constrained |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the execution chain works |
| [docs/ROADMAP.md](docs/ROADMAP.md) | V0.5.9 → V0.7.0 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | What changed in each version |
| [docs/README.md](docs/README.md) | Full documentation index |

## Project Status Note

ANU is a personal research and engineering project under active development.
It is designed for one specific device and is not yet packaged for general
installation.
