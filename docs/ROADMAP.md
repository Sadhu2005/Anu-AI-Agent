# ANU Roadmap: V0.6.0 → V0.7.0

## The Most Important Rule

Do **not** jump from reliable execution to an "AI brain". Build in this order:

```text
SAFE EXECUTION
      ↓
RELIABLE EXECUTION
      ↓
GOAL / PLAN
      ↓
TOOLS
      ↓
OBSERVATION
      ↓
MEMORY
      ↓
INTELLIGENCE
      ↓
V0.7.0 AUTONOMY
```

Otherwise a smart planner ends up sitting on an unreliable execution substrate.
The reasoning behind this order is explained in [WHY.md](WHY.md).

## Maturity

```text
Foundation   ████████████████████  100%
V0.5.6       ████████████████████  100%
V0.5.7       ████████████████████  100%
V0.5.8       ████████████████████  100%
V0.5.9       ████████████████████  100%
V0.6.0       ████████████████████  100%
V0.6.1       ████████████████████  100%
V0.6.1b      ████████████████████  100% (with V0.6.2)
V0.6.2       ████████████████████  100%
V0.6.3       ████████████████████  100%
V0.6.4       ████████████████████  100%
V0.6.5       ████████████████████  100%
V0.6.6       ████████████████████  100%
V0.6.7       ████████████████████  100%
V0.6.8       ████████████████████  100%
V0.6.9       ████████████████████  100%
V0.7.0       ░░░░░░░░░░░░░░░░░░░░  0%
```


## Plan

| Version | Theme | Core outcome | Depends on |
|---|---|---|---|
| [V0.5.9](versions/v0.5.9.md) | Autonomous production execution | A real task is mutated, tested, committed and completed by the production service | V0.5.8 |
| [V0.6.0](versions/v0.6.0.md) | Reliable execution | Retry, crash/restart recovery, idempotent mutations | V0.5.9 |
| [V0.6.1](versions/v0.6.1.md) | Goal → Plan → Task Graph | A goal expands into a dependent task graph | V0.6.0 |
| [V0.6.2](versions/v0.6.2.md) | Capabilities | Every action is a policy-checked, audited capability | V0.6.0 |
| [V0.6.3](versions/v0.6.3.md) | Memory / observation | Observations become reusable memory | V0.6.2 |
| [V0.6.4](versions/v0.6.4.md) | Project intelligence | Structured project model the planner can reason over | V0.6.3 |
| [V0.6.5](versions/v0.6.5.md) | Verification / evaluation | Formal ACCEPT / RETRY / ROLLBACK decision | V0.6.2, V0.6.4 |
| [V0.6.6](versions/v0.6.6.md) | Resource-aware intelligence | Work selection driven by machine conditions | V0.6.0 |
| [V0.6.7](versions/v0.6.7.md) | Human approval boundary | AUTONOMOUS / APPROVAL / DENY classification | V0.6.2 |
| [V0.6.8](versions/v0.6.8.md) | Long-running agent | Survives reboot, crash, partial execution and resumes | V0.6.0 |
| [V0.6.9](versions/v0.6.9.md) | Pre-7.0 hardening (security release) | Security gaps G1–G3 closed with evidence, consistency, failure injection; feature freeze | all V0.6.x |
| [V0.7.0](versions/v0.7.0.md) | Real ANU agent runtime | Goal → plan → execute → verify → remember, as one system | V0.6.9 |

## Milestone Reached (V0.5.9, 5 Oct 2026)

Proven on the Raspberry Pi through the production service:

```text
TASK 186 → production scheduler → AgentController → Task Engine
→ Safe Mutation → checkpoint → app.py mutation → python_test PASS
→ COMMITTED → mutation transaction persisted → TASK COMPLETE
```

and the failure path (task 187): mutation → TEST_FAILURE → rollback →
integrity verified → ROLLED_BACK. This is ANU's first demonstrated
autonomous production execution ([evidence](versions/v0.5.9.md#evidence)).

## Milestone Reached (V0.6.0, 16 Oct 2026)

Proven on the Raspberry Pi through the production service:

```text
mid-mutation restart / reboot  → cycle finishes → COMMITTED once
mid-mutation SIGKILL / power cut → Recovery reconciles → retry after
  2 min → COMMITTED once (same idempotency key)
```

Evidence: tasks 224–227 and [versions/v0.6.0.md](versions/v0.6.0.md).

## Immediate Target

[V0.7.0](versions/v0.7.0.md) real ANU agent runtime.

V0.6.9 is frozen (tag `v0.6.9`): G1–G3 closed with Pi negative evidence,
`BOUNDARY_CHECK` ok, feature freeze before V0.7.0
([versions/v0.6.9.md](versions/v0.6.9.md)).

## Model gateway (separate release)

The API-first **`anu-model-gateway`** service ([MODEL-STRATEGY.md](MODEL-STRATEGY.md),
ADR-011) remains **its own release** after [V0.6.5](versions/v0.6.5.md) /
[V0.6.7](versions/v0.6.7.md) capability and approval work — not bundled into
those version tags (review §8).

## Beyond V0.7.0

V0.8 (capability), V0.9 (goal-level autonomy), and V1.0 (trusted personal
agent) are described as direction, not commitments, in [VISION.md](VISION.md).
