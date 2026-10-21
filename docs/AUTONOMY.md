# ANU Autonomy (V0.9 → lab-repo)

Junior-dev mode: you give a **goal (prompt)** and a **git repo**. ANU plans, implements on `agent/*`, runs a test matrix, diagnoses failures, commits, pushes each verified step, and continues until the goal is verified — without asking you to type `next`.

ANU itself lives in **Anu-AI-Agent** (Python on Pi). Lab targets such as [anu-agent-test](https://github.com/Sadhu2005/anu-agent-test) receive the commits. This is **not** a TypeScript rewrite of the agent.

## Operator prompt format

```text
Goal: Build X with tests
Repo: https://github.com/Sadhu2005/anu-agent-test
Criteria: unit+integration green; work on agent/*; humans merge
```

## Assign (goal + repo)

```bash
python3 /srv/anu/bin/anu-project-assign-v0.1.py \
  --db /srv/anu/brain/anu-brain.db \
  assign \
  --project-key anu-agent-test \
  --github Sadhu2005/anu-agent-test \
  --goal "Add a greet helper with full test matrix" \
  --objective "Ship greet + counter with unit/integration/functional/e2e/regression" \
  --criteria "matrix green; pushed to agent/*" \
  --autonomous \
  --expand
```

`--github` clones an allow-listed HTTPS repo (or reuses a clean workspace). Dirty trees are preserved — ANU will not wipe them.

Global flags (`--db`, `--config`, `--workspace`) must come **before** the subcommand.

## Step cadence

```text
ACCEPT (verification) → test matrix gate → commit on agent/* → push → next READY task
```

Config: `push_after_accept: true` in [config/anu-autonomy.yaml](../config/anu-autonomy.yaml).

## Test matrix

Ordered layers: **unit → integration → functional → e2e → regression**.

- Missing layers → `SKIPPED` (not a pass).
- Failed layer → write `.anu/DIAGNOSIS.md`, REPLAN/fix within retry budget, no push.
- Never claim pass without runner exit code 0.

Detector/runner: `bin/anu-test-matrix-v0.1.py`. Diagnose: `bin/anu-failure-diagnose-v0.1.py`.

## Control

```bash
python3 bin/anu-autonomy-controller-v0.1.py status
python3 bin/anu-autonomy-controller-v0.1.py pause --goal 15 --reason "review"
python3 bin/anu-autonomy-controller-v0.1.py resume --goal 15
python3 bin/anu-autonomy-controller-v0.1.py stop --goal 15
python3 bin/anu-autonomy-controller-v0.1.py run-once --goal 15
```

Or use the dashboard (Pause / Resume / Stop). Pause blocks new work; resume continues from the last ACCEPT checkpoint. Stop ends the run without merge.

## Dashboard (V0.9.10+)

Local-only UI on `127.0.0.1:8787` (SSH tunnel; see [PI-OPS.md](PI-OPS.md)).

| Tab | Use |
|---|---|
| **Overview** | System gauges, autonomous goals table, current tasks, stale heartbeat |
| **Active Goal** | Workspace/repo path, checklist + evidence, timeline, progress, message form |
| **Runtime** | Events, logs, LLM providers, git (branch/SHA/remote), tests, **test matrix** |
| **Approvals** | Pending Approve / Deny (incl. gated PR) |
| **Assign** | Goal + optional GitHub repo URL |

## What auto-approves

When `autonomous: true` and `auto_approve: true`:

- research, github reads, sandboxed mutations, tests, commits, push to `agent/*`

Always denied (pending human / never auto):

- deploy, production infra, secrets, force-push, push to default branch, delete, new repos, **auto-merge**

## LLM failover

Order in `config/anu-llm-providers.yaml`: LM Studio → Groq → Gemini → OpenRouter free.
Transport/timeout/rate-limit → failover. **Test failures are not provider outages.**

## Progress + locks

- `AUTONOMY_PROGRESS.md` in project workspace (uses `projects.workspace` when set).
- One writer lock per workspace: `.anu/WRITER.lock`.

## Stop conditions

Missing requirements / policy deny / external outage / budgets (time, iterations, fingerprints, matrix retry class) / verified acceptance.

## Versions

| Version | Focus |
|---------|--------|
| [v0.9.11](versions/v0.9.11.md) | Lab bootstrap + assign goal+repo |
| [v0.9.12](versions/v0.9.12.md) | Step commit/push after ACCEPT |
| [v0.9.13](versions/v0.9.13.md) | Test matrix + diagnose |
| [v0.9.14](versions/v0.9.14.md) | Generator / stages |
| [v0.9.15](versions/v0.9.15.md) | Unattended 5-task lab E2E |
| [v1.0](versions/v1.0.md) | Gated PR + hardening freeze |
