# ANU Changelog

Short summary per version. Full detail, design, tests, and evidence are in
[versions/](versions/).

## [V0.8.2] — 2026-10-18 · tag `v0.8.2`

Richer multi-step planning graphs (research → design → implement → test → verify).

- Template `multi_stage_goal` (default expand / LLM fallback)
- LLM prompt prefers stage roles; `improve_sandbox` kept
- Pack: `run_v0_8_2_regression.py`
- Pi: regression **2/2**; goal 13 tasks 293–297 (5-stage)

## [V0.8.1] — 2026-10-18 · tag `v0.8.1`

Structured recovery from verification (retry / wait / block / escalate).

- `anu-verification-v0.1.py`: `recovery_policy_for_verification`
- `anu-task-engine-v0.5.3.py`: `retry_after` + `TASK_RECOVERY_POLICY`
- Pack: `run_v0_8_1_regression.py`
- Pi: regression **2/2**; RETRY→retry/escalate, ROLLBACK→block

## [V0.8.0] — 2026-10-18 · tag `v0.8.0`

Self-observation from verification decisions (ACCEPT / RETRY / ROLLBACK).

- `anu-observation-memory-v0.1.py`: `record_verification_outcome`
- `anu-verification-v0.1.py`: best-effort memory write after persist
- Pack: `run_v0_8_0_regression.py`
- Pi: regression **2/2**; hint `verification ACCEPT: python_test`

## [V0.7.6] — 2026-10-18 · tag `v0.7.6`

LLM-aware goal replan via recovery (same `planner.mode` as goal-cycle).

- `anu-goal-recovery-v0.1.py`: llm_optional / template_only load
- `GOAL_REPLANNED` metadata includes `source`
- Pack: `run_v0_7_6_regression.py`
- Pi: regression **2/2**; replan goal 12 `source=llm`

## [V0.7.5] — 2026-10-18 · tag `v0.7.5`

Activity reports (“What did you do?”) — read-only brain assembly.

- `anu-activity-report-v0.1.py`: `goal --goal-id` / `recent --limit`
- Pack: `run_v0_7_5_regression.py`
- Pi: regression **1/1**; goal 12 report `ok` (`source_hint=llm`)

## [V0.7.4] — 2026-10-18 · tag `v0.7.4`

LLM planner prompt harden + one retry; gateway `tool_choice` pass-through.

- Stronger no-tools system prompt; retry on tool-echo / empty plan
- No retry on disallowed operations
- Pack: `run_v0_7_4_regression.py`
- Pi: regression **3/3**; live expand goal 12 `source=llm` (`ok_retry`)

## [V0.7.3] — 2026-10-18 · tag `v0.7.3`

LLM planner unwrap for LM Studio tool-call envelopes.

- `coerce_plan_object`: `parameters.plan`, nested JSON / Python-literal, OpenAI
  `tool_calls`
- Pack: `run_v0_7_3_regression.py`
- Pi: regression **2/2**; live expand still may `template_fallback` on tool-echo

## [V0.7.2] — 2026-10-18 · tag `v0.7.2`

Optional LLM planner via model gateway; template fallback.

- `anu-llm-planner-v0.1.py`: propose/validate steps; only `python_test` /
  `python_compile`; gateway down or bad JSON → `improve_sandbox`
- Config `planner:` block (`mode: llm_optional | template_only`)
- Goal-cycle loads LLM planner when `llm_optional`
- Pack: `run_v0_7_2_regression.py` (LLM + gateway tests)
- Pi: regression **3/3**; live expand `template_fallback` (LM Studio tool-JSON)

## [V0.7.1] — 2026-10-18 · tag `v0.7.1`

Model gateway (OpenAI-compatible): LM Studio / cloud by config + API key.

- Docs: [MODEL-GATEWAY.md](MODEL-GATEWAY.md), [versions/v0.7.1.md](versions/v0.7.1.md)
- Config: `config/anu-model-gateway.yaml` (no secrets)
- Gateway: `serve` Unix socket + SO_PEERCRED; CLI `health` / `chat` / `reason`
- Client: `anu-model-client-v0.1.py` for `anu-agent`
- systemd: `anu-model-gateway.service` with `LoadCredential=`
- ADR-011 accepted; URL-as-key rejected
- Pi: regression **2/2**; unit active; client health/chat `ok: true` → LM Studio
- Deferred: optional LLM planner via gateway (next slice)

## [V0.7.0] — 2026-10-18 · tag `v0.7.0`

Real ANU agent runtime (integration of V0.6.x).

- Goal cycle glue: expand pending + sync statuses + remember on complete
- Runtime: wake re-eval → expand → schedule → goal sync each tick
- Production deploy gate: `safe-mutation --production`
- Pi: regression **8/8**; failure injection **6/6**; heartbeat `running`
  (7 active goals, startup recovery)

## [V0.6.9] — 2026-10-18 · tag `v0.6.9`

Pre-7.0 hardening (security release) · **feature freeze** before V0.7.0.

- G1/G2: `secrets` + `tests` → ReadOnlyPaths (unit + `anu-policy.yaml`)
- Boundary self-check at runtime startup; fail-closed scheduling
- ADR-015 (G4 logs), ADR-016 (G6 operator docker)
- Pi: regression 3/3; failure injection 5/5; touch secrets/tests/bin denied;
  `BOUNDARY_CHECK` id 28798 `ok` (EROFS)

## [V0.6.8] — 2026-10-18 · tag `v0.6.8`

Long-running agent: durable resume across restart.

- `anu-startup-recovery-v0.1.py` + `wake_conditions` (`0010_v0_6_8.sql`)
- Startup: mutation recover → expire approvals → evaluate wakes → inventory
- SIGTERM parks `anu-state.json`; heartbeat fields; log rotation
- Runtime wired to full startup sequence
- Pi: regression 4/4; startup heartbeat; rotated `anu-mutations.jsonl` (6.1 MB)

## [V0.6.7] — 2026-10-18 · tag `v0.6.7`

Human approval boundary: AUTONOMOUS / APPROVAL / DENY.

- `anu-approval-service-v0.1.py` + enriched `approvals` (`0009_v0_6_7.sql`)
- CLI request / list / approve / reject / expire; task WAITING → pending on grant
- Gateway records approval when Governor returns APPROVAL
- Production deploy gate via project intelligence rule
- Pi: migrate + regression 4/4; approve id=1 → task 257 pending

## [V0.6.6] — 2026-10-18 · tag `v0.6.6`

Resource-aware intelligence: work selection from machine conditions.

- `anu-resources.yaml` authoritative via `anu-resource-policy-v0.1.py`
- Temperature hysteresis (`resume_celsius`); Guardian state file
- Scheduler RUN/WAIT/DENY (heavy low RAM, hot host, disk DENY)
- Task Engine `MAX_ATTEMPTS` from policy; consistency gate (G5)
- Pi: regression 4/4; consistency ok; Guardian NORMAL @ 47.95 °C

## [V0.6.5] — 2026-10-18 · tag `v0.6.5`

Verification / evaluation: formal ACCEPT | RETRY | ROLLBACK per attempt.

- `verification_records` (`0008_v0_6_5.sql`) + `anu-verification-v0.1.py`
- Safe Mutation: post-mutation digests + diff-scope before COMMIT
- Task engine: COMPLETE only with ACCEPT; decisions audited
- Goal aggregator: completed only when every task has ACCEPT
- Pi: migrate + regression 6/6; live ACCEPT `verification_records.id=1` (task 257)
- CI: install PyYAML for portable tests

## [V0.6.4] — 2026-10-18 · tag `v0.6.4`

Project intelligence: registered projects get a refreshed structured model.

- `anu-projects.yaml` is the explicit allow-list; Brain `projects` + `project_models`
- Read-only scanner (files/tests/deps/history/known problems from memory)
- Planner and Safe Mutation refuse unregistered projects
- Pi: sync 5 projects; scan `sandbox-test-project`; regression 2/2

## [V0.6.3] — 2026-10-18 · tag `v0.6.3`

Memory / observation: capability outcomes become durable, retrievable memory.

- `observations` capture + additive `agent_memories` (provenance, TTL, prune)
- Capability adapter records results; template expand uses retrieval hints
- Portable regression 2/2; Pi live: invoke → observation/memory id 1 → retrieve

## [V0.6.2] — 2026-10-18 · tag `v0.6.2`

Capabilities: policy-checked, audited invoke through the registry and gateway.

- Capability registry over gateway allow-list + execution profiles
- `evaluate_request` / deny audit (`CAPABILITY_*`); `invoke` → gateway `run`
- V0.6.1b boundary hardening (paths, hardened host git, FK `db_connect`,
  audit drop stderr, sandboxd) + review P0 plan/freeze/superseded fixes
- YAML↔`COMMANDS` drift assert; `filesystem.write` deferred
- Portable regression 9/9; Pi live: `git.status` hardened invoke + denials
- Post-freeze: `anu-capability-adapter-v0.1.py` — Safe Mutation ops via registry

## [V0.6.1] — 2026-10-16 · tag `v0.6.1`

Goal → Plan → Task Graph: a goal expands into a dependent task graph.

- Agent admission: ineligible pending tasks are blocked alone (`contract`);
  the scheduler cycle continues
- Goal service CRUD on the existing `goals` table
- Versioned `goal_plans` (`AgentPlan` JSON); new active plan supersedes
- Template planner `improve_sandbox` → 6 chained tasks + execution specs
- Goal status derived from the task graph (aggregator)
- Failure policies: `block` or `replan` with recorded reason/events
- Regression 6/6; live evidence on Pi (goal 6 readiness chain; goal 7 block)

## [V0.6.0] — 2026-10-16 · tag `v0.6.0`

Reliable autonomous execution: crash, kill, and reboot mid-mutation no
longer leave a project half-changed.

- Graceful stop: `KillMode=mixed`, `TimeoutStopSec=420`, ordered after
  `anu-sandbox.service`; SIGTERM finishes the cycle in flight
- Write-ahead `STARTED` transaction before any file change; idempotency
  key with at most one `COMMITTED` per key (`ALREADY_COMMITTED`)
- Startup recovery under an exclusive execution lock: reconcile
  `STARTED` transactions and in-flight tasks (ADR-012, ADR-013)
- Retry only for `interrupted` work: `pending` with 2- then 10-minute
  backoff, max 3 attempts, then `blocked`; other classes stay failed or
  blocked
- Live evidence on the Pi: restart and reboot complete gracefully; kill
  and forced reboot roll back or leave untouched, then retry once
- Nine V0.6.0 test files (38 cases); full regression 52 files on deploy
- Known follow-up (closed in V0.6.1): a goal-less pending task failed the
  whole scheduler cycle — fixed by agent admission

## [V0.5.9] — 2026-10-05 · tag `v0.5.9`

Autonomous production execution: **first autonomous commit and rollback by
the production service.**

- Runtime runs one scheduler tick per cycle, only when the Guardian reports `NORMAL`
- Scheduler lock file, `max_tasks = 1`, failures logged without crashing the runtime
- Integer `goal_id` from SQLite normalised to string for `AgentGoal`
- Safe Mutation `resolve_changes_file`: relative changes files resolve inside the project, not the process working directory; absolute paths keep the earlier contract
- `/srv/anu/checkpoints` writable under the systemd service
- Resource policy aligned across config files and deployed: 60 °C warning, 65 °C pause, 55 °C resume, 50 GB free disk, 3 attempts per task
- Task 186 COMMITTED and completed autonomously; task 187 ROLLED_BACK with verified integrity; task 192 COMMITTED
- Full regression 42 / 42
- Tests: `test_runtime_scheduler_tick_v0_5_9`, `test_agent_task_adapter_goal_id_v0_5_9`, `test_safe_mutation_changes_path_v0_5_9`

## [V0.5.8] — 2026-10-04 · `1996199`

- Agent-aware scheduler lifecycle: scheduler wakes the agent, not the bare Task Engine
- Tests: `test_agent_wake_binding_v0_5_8`, `test_real_agent_wake_chain_v0_5_8`

## [V0.5.7] — 2026-10-03 · `718441c`

- WAITING is a resumable state; waiting goals re-wake and continue
- Tests: `test_agent_waiting_lifecycle_v0_5_7`, `test_waiting_rewake_v0_5_7`

## [V0.5.6] — 2026-10-03 · `9fe804f`

- Task → AgentGoal adapter, task-id bridge, task execution adapter and runtime
- Agent execution results preserved end-to-end
- Tests: five `*_v0_5_6` suites

## [V0.5.5] — 2026-10-03 · tag `v0.5.5` · `36c666a`

- Agent model (`AgentGoal`, `AgentAction`, `AgentPlan`, `AgentResult`)
- Agent state machine and controller
- Scheduler lifecycle runtime
- Tests: agent model, state machine, controller, scheduler lifecycle

## [V0.5.4] — 2026-10-03 · `d896412`

- Dependency wake planner, consumer, execution adapter
- Execution specification per task (project, changes file, operation)
- Scheduler candidate discovery, READY → WAKE plan, dispatch
- Bounded production scheduler cycle
- Tests: fifteen `*_v0_5_4` suites across ten checkpoint commits

## [V0.5.3] — 2026-10-03 · tag `v0.5.3` · `1b5968b`

- Canonical Task Engine baseline (`anu-task-engine-v0.5.3.py`)
- Task dependency graph with cycle prevention
- Dependency admission: only `READY` tasks start
- All configuration brought under version control

## [V0.5.2] — 2026-10-03 · tag `v0.5.2` · `067e58d`

- First git commit (26 files, 9,493 lines)
- Checkpoint corruption detection, concurrent contention, multi-file mutation, retry exhaustion

## [Foundation] — up to V0.5.1

- Runtime, Resource Guardian and Governor, command gateway, executor,
  execution profiles, Docker sandbox daemon, result normaliser
- Safe Mutation v0.1 → v0.2 with checkpoints and verified rollback
- Task Engine v0.3 → v0.5.1 with explicit retry semantics
