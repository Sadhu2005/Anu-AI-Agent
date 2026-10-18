# ANU Documentation

ANU is a self-hosted autonomous agent runtime that runs on a Raspberry Pi 5
(Debian 13, 8 GB RAM) under `/srv/anu`, as the `anu-agent` user, launched by
`anu-agent.service`.

These documents are the single place where every ANU version is recorded:
what was planned, what was built, how it was proven, and what was left for
the next version.

## Documents

| Document | Purpose |
|---|---|
| [WHY.md](WHY.md) | Why ANU is built safety-first, and the principles that follow |
| [DESIGN-PHILOSOPHY.md](DESIGN-PHILOSOPHY.md) | Time-rich, compute-constrained: why ANU runs slowly and safely on a Raspberry Pi |
| [MODEL-STRATEGY.md](MODEL-STRATEGY.md) | How intelligence plugs in: API-first model gateway, budget, privacy, local models later |
| [VISION.md](VISION.md) | What ANU becomes after V0.7.0: direction from V0.8 to V1.0 |
| [DECISIONS.md](DECISIONS.md) | Architecture decision log: what was decided, why, and at what cost |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Current execution chain and target V0.7.0 architecture |
| [ROADMAP.md](ROADMAP.md) | V0.6.0 → V0.7.0 plan, ordering rules, maturity |
| [HISTORY.md](HISTORY.md) | How ANU was built, from the first config file to the first autonomous task |
| [CHANGELOG.md](CHANGELOG.md) | Short summary of every version |
| [versions/](versions/) | One file per version (completed and planned) |
| [versions/_TEMPLATE.md](versions/_TEMPLATE.md) | Template for new version documents |

### Reference

| Document | Purpose |
|---|---|
| [SECURITY.md](SECURITY.md) | What ANU must never do, permission layers, secrets, public-repo rules, known gaps |
| [WORKFLOW.md](WORKFLOW.md) | How a version is developed, tested, frozen, tagged, and deployed |
| [SETUP.md](SETUP.md) | Rebuilding ANU on a fresh Raspberry Pi (V0.5.9 reconstruction runbook) |
| [OPERATIONS.md](OPERATIONS.md) | Runbook: service control, health, logs, tasks, backups |
| [CONFIGURATION.md](CONFIGURATION.md) | Every config file, who reads it, and what each key means |
| [DATABASE.md](DATABASE.md) | Brain schema: tables, relationships, integrity rules, migration policy, schema status |
| [TESTING.md](TESTING.md) | How to run tests, suites by version, isolation caveats |
| [GLOSSARY.md](GLOSSARY.md) | ANU terminology |
| [LESSONS-LEARNED.md](LESSONS-LEARNED.md) | Real problems, causes, and the rules they taught |

## Version Index

### Completed

| Version | Title | Status | Git reference |
|---|---|---|---|
| [Foundation](versions/v0.5.1-foundation.md) | Gateway, sandbox, executor, safe mutation, task engine v0.3 → v0.5.1 | Frozen | included in `v0.5.2` |
| [V0.5.2](versions/v0.5.2.md) | Mutation robustness and concurrency | Frozen | tag `v0.5.2`, branch `master` |
| [V0.5.3](versions/v0.5.3.md) | Task dependency graph and admission | Frozen | tag `v0.5.3`, branch `v0.5.3-dev` |
| [V0.5.4](versions/v0.5.4.md) | Scheduler, wake planning, execution specification | Frozen | branch `v0.5.4-dev` |
| [V0.5.5](versions/v0.5.5.md) | Agent model, controller, scheduler lifecycle | Frozen | tag `v0.5.5` |
| [V0.5.6](versions/v0.5.6.md) | Task ↔ agent adapters, execution result preservation | Frozen | branch `v0.5.6-integration` |
| [V0.5.7](versions/v0.5.7.md) | Real WAITING lifecycle and re-wake | Frozen | branch `v0.5.7-integration` |
| [V0.5.8](versions/v0.5.8.md) | Agent-aware scheduler lifecycle boundary | Frozen | branch `v0.5.8-integration` |
| [V0.5.9](versions/v0.5.9.md) | Autonomous production execution | Frozen | tag `v0.5.9` |
| [V0.6.0](versions/v0.6.0.md) | Reliable autonomous execution | Frozen | tag `v0.6.0`, branch `v0.6.0-integration` |
| [V0.6.1](versions/v0.6.1.md) | Goal → Plan → Task Graph | Frozen | tag pending `v0.6.1`, branch `v0.6.1-integration` |

### In progress

| Version | Title | Status |
|---|---|---|
| [V0.6.2](versions/v0.6.2.md) | Capabilities | Next |

### Planned

| Version | Title |
|---|---|
| [V0.6.2](versions/v0.6.2.md) | Tool / capability architecture |
| [V0.6.3](versions/v0.6.3.md) | Memory / observation layer |
| [V0.6.4](versions/v0.6.4.md) | Project intelligence |
| [V0.6.5](versions/v0.6.5.md) | Verification / evaluation |
| [V0.6.6](versions/v0.6.6.md) | Resource-aware intelligence |
| [V0.6.7](versions/v0.6.7.md) | Human approval boundary |
| [V0.6.8](versions/v0.6.8.md) | Long-running agent |
| [V0.6.9](versions/v0.6.9.md) | Pre-7.0 hardening |
| [V0.7.0](versions/v0.7.0.md) | Real ANU agent runtime |

## Working Rules

1. **One version, one branch.** Work happens on `vX.Y.Z-integration` (or
   `-dev`); a version is frozen by a commit and, ideally, a tag.
2. **Every version has tests.** Test files are named `tests/test_<feature>_vX_Y_Z.py`
   and earlier regressions must keep passing.
3. **Freeze = proven, not written.** A version is only marked *Frozen* when its
   exit criteria were demonstrated, and the evidence is recorded in its document.
4. **Docs are written here, in the Cursor workspace**, committed with the
   version, then pushed to GitHub. The Raspberry Pi (`/srv/anu`) is the runtime,
   not the place where docs are edited.
5. **Runtime state is never versioned:** `brain/`, `checkpoints/`, `home/`,
   `logs/`, `runtime/`, `workspace/`, `browser/`, `sandbox/`, `secrets/`.

## Status Legend

- **Frozen**: complete, proven, and not to be changed except through a later version
- **In progress**: actively being implemented
- **Planned**: scoped, not started
