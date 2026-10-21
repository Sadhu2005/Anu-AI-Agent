# ANU Autonomy (V0.9)

Junior-dev mode: assign a goal, ANU continues until done or a real boundary.

## Assign an autonomous goal

```bash
python3 /srv/anu/bin/anu-project-assign-v0.1.py \
  --db /srv/anu/brain/anu-brain.db \
  assign \
  --project-key sandbox-test-project \
  --goal "Add a simple todo list" \
  --objective "Ship a working todo list with tests" \
  --criteria "TodoList add/list works; unit tests pass" \
  --autonomous \
  --expand
```

Global flags (`--db`, `--config`, `--workspace`) must come **before** the subcommand.

## Control

```bash
python3 bin/anu-autonomy-controller-v0.1.py status
python3 bin/anu-autonomy-controller-v0.1.py pause --goal 15 --reason "review"
python3 bin/anu-autonomy-controller-v0.1.py resume --goal 15
python3 bin/anu-autonomy-controller-v0.1.py stop --goal 15
python3 bin/anu-autonomy-controller-v0.1.py run-once --goal 15
```

Or use the dashboard (Pause / Resume / Stop).

## What auto-approves

When `autonomous: true` and `config/anu-autonomy.yaml` `auto_approve: true`:

- research, github reads, sandboxed mutations, tests, commits, push to `agent/*`

Always denied (pauses for you):

- deploy, production infra, secrets, force-push, push to default branch, delete, new repos

## LLM failover

Order in `config/anu-llm-providers.yaml`: LM Studio → Groq → Gemini → OpenRouter free.
Memory is in the brain `llm_context` table — switching providers does not drop state.

## Progress file

`AUTONOMY_PROGRESS.md` is written into the project workspace (atomic replace).
It is ignored by the mutation diff-scope verifier.

## Stop conditions

ANU stops only for missing requirements / policy deny / external outage /
budget (time, iterations, fingerprints) / verified acceptance.
