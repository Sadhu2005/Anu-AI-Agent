# ANU Brain Schema

> **The ANU Brain is the durable state and operational memory of the agent.**

| | |
|---|---|
| **File** | `/srv/anu/brain/anu-brain.db` |
| **Engine** | SQLite, WAL mode (`-wal` and `-shm` files alongside) |
| **Owner** | `anu-agent`, directory mode `700` |
| **Size** | about 10 MB (October 2026) |
| **Versioned in git** | No. Runtime state; schema and migrations only (section 43) |

The Brain stores the structured state ANU needs to remember goals, track
tasks, understand dependencies, describe execution, record events,
observations, failures, and resource usage, track mutations, create
checkpoints, record tests, and track projects, commits, and pull requests.

The Brain is not the model. It is not the filesystem. It is not the execution
engine. It is the **durable state layer connecting those components**.

---

## 1. Design Principles

| Principle | Meaning |
|---|---|
| **Durable** | Important agent state survives runtime restarts |
| **Structured** | State is structured records, not only log text |
| **Relational** | goal → task, task → dependency, task → execution spec, task → checkpoint / test result / mutation / event, project → goal are explicit |
| **Auditable** | Important state changes are traceable through events and timestamps |
| **Minimal** | Only what ANU needs to reason about its work, not a dump of the filesystem or every external source |
| **Migration-safe** | Schema changes go through explicit migrations; task and mutation history is never casually destroyed or rewritten |

---

## 2. Current Table Inventory

```text
actions                 errors                  research
agent_events            goals                   resource_usage
agent_metadata          mutation_transactions   task_dependencies
approvals               observations            task_execution_specs
checkpoints             projects                tasks
commits                 pull_requests           test_results
```

Plus `sqlite_sequence`, maintained by SQLite for `AUTOINCREMENT` tables.

### Where the schema comes from

Most tables were created by the **initial brain schema** on 2 Oct 2026, which
is **not in this repository**. Later versions add their own tables and
columns:

| Change | Created by |
|---|---|
| `mutation_transactions` | `anu-safe-mutation.py` (Foundation), `CREATE TABLE IF NOT EXISTS` |
| `mutation_transactions` audit columns + unique index | `anu-safe-mutation-v0.2.py`, additive `ALTER TABLE` when missing |
| `agent_events` attribution columns | Brain Schema v0.2 migration, 3 Oct 2026, run manually; script not in git (backup `anu-brain.db.backup-v0.2-20261003T080022Z`) |
| `task_dependencies` + indexes | `anu-task-graph-v0.1.py` (V0.5.3) |
| `task_execution_specs` | `anu-task-execution-spec-v0.1.py` (V0.5.4) |

---

## 3. High-Level Data Model

```text
                PROJECT
                   │
                  GOAL
                   │
                  TASK
         ┌─────────┼──────────┐
    DEPENDENCY  EXEC SPEC   EVENTS
         │         │
         │    SAFE MUTATION
         │         ├── CHECKPOINTS
         │         ├── TEST RESULTS
         │         └── MUTATION TRANSACTIONS
         │
    WAIT / READY
```

Attached to the task lifecycle: observations, errors, resource usage,
approvals, agent events. Project history: commits, pull requests.

---

## 4. Goals

```sql
CREATE TABLE goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);
```

Verified in the V0.5.9 AgentGoal contract inspection. A goal is the
higher-level objective that tasks belong to, e.g. *"Improve the portfolio
application's test reliability."* `tasks.goal_id` links work back to it.

Goals become the top-level unit of work in [V0.6.1](versions/v0.6.1.md).

---

## 5. Tasks

```sql
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER,
    parent_task_id INTEGER,
    task_key TEXT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 5,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(goal_id) REFERENCES goals(id),
    FOREIGN KEY(parent_task_id) REFERENCES tasks(id)
);
```

Verified against the live V0.5.9 schema. The task is the fundamental unit of
executable work.

| Column | Meaning |
|---|---|
| `id` | Immutable database identity |
| `goal_id` | Parent goal (integer in SQLite; normalized to `str` for `AgentGoal` at the adapter boundary since V0.5.9) |
| `parent_task_id` | Optional parent task |
| `task_key` | Human/system-readable identifier; the Task Engine defaults it to `TASK-<10 hex>` |
| `title`, `description` | Short and detailed description |
| `status` | Persisted lifecycle status (section 6) |
| `priority` | Scheduling priority, default 5 |
| `attempts` | Attempts used; `MAX_ATTEMPTS = 3` |
| `created_at`, `updated_at` | Lifecycle timestamps |

---

## 6. Task Status

`tasks.status` is written by the Task Engine (v0.5.3). It persists
**every** stage, not only outcomes:

```text
pending → planning → ready → running → verifying → completed
                                                  → failed    (retryable)
                                                  → blocked   (terminal)
```

| Set (in code) | Values |
|---|---|
| `RUNNABLE` | `pending`, `ready` |
| `TERMINAL` | `completed`, `blocked` |
| Retryable | `failed` (until attempts are exhausted, then `blocked`) |

A task found in `planning`, `running`, or `verifying` after a crash is an
interrupted run. Detecting and recovering it is [V0.6.0](versions/v0.6.0.md).

The **Agent Controller** has a separate, in-process lifecycle
(`AgentState` in `anu-agent-runtime-v0.1.py`):

```text
NEW · READY · PLANNING · EXECUTING · OBSERVING · WAITING · RETRY · COMPLETE · FAILED
```

These are intentionally different concepts. `tasks.status` is persisted task
state, and the Agent Controller state is the current in-process agent
lifecycle. That distinction must stay explicit.

---

## 7. Parent Tasks

```text
Goal
 ├── Task A
 │    ├── Task A.1
 │    └── Task A.2
 └── Task B
```

Represented by `tasks.parent_task_id → tasks.id`. Larger tasks decompose
without a separate hierarchy table.

---

## 8. Task Dependencies

```sql
CREATE TABLE task_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    depends_on_task_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, depends_on_task_id),
    CHECK(task_id != depends_on_task_id),
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY(depends_on_task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
```

Indexes on `task_id` and `depends_on_task_id`. Cycles are rejected in code
(`would_create_cycle`) before insertion.

`task_id = B, depends_on_task_id = A` means *B depends on A* (e.g. 1002 →
1001). The scheduler uses this graph to decide whether a task can become
ready.

---

## 9. Task Execution Specifications

```sql
CREATE TABLE task_execution_specs (
    task_id INTEGER PRIMARY KEY,
    project TEXT NOT NULL,
    changes_file TEXT NOT NULL,
    operation TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
```

One-to-one with `tasks`, keyed by `task_id`.

- `tasks` answers *"What work exists?"*
- `task_execution_specs` answers *"How is this task executed?"*

```text
task_id       186
project       sandbox-test-project
changes_file  .anu-v0.5.9-changes.json     (resolved inside the project since V0.5.9)
operation     python_test
```

Task intent and execution details are different contracts.

---

## 10. Agent Events

`agent_events` is ANU's primary chronological event stream.

Original columns: `id`, `event_type`, `message`, `metadata` (JSON),
`created_at`. The Brain Schema v0.2 migration added attribution:

```sql
ALTER TABLE agent_events ADD COLUMN task_id INTEGER;
ALTER TABLE agent_events ADD COLUMN goal_id INTEGER;
ALTER TABLE agent_events ADD COLUMN parent_task_id INTEGER;
ALTER TABLE agent_events ADD COLUMN run_id TEXT;
ALTER TABLE agent_events ADD COLUMN attempt INTEGER;
```

with indexes `idx_agent_events_task_id` and `idx_agent_events_run_id`. The
JSON `metadata` was deliberately kept.

Event types include Task Engine lifecycle events (`TASK_CREATED`,
`TASK_STATE`, `TASK_COMPLETED`, `TASK_DEPENDENCY_WAITING`, …),
`RESOURCE_CHECK`, `MUTATION_CHECKPOINT`, and `MUTATION_TRANSACTION`. A
recorded lifecycle:

```text
TASK_CREATED → TASK_STATE: planning → ready → running → verifying → TASK_COMPLETED
```

---

## 11. Actions

Verified columns: `id`, `task_id`, `action_type`, `command`, `status`,
`output`, `started_at`, `finished_at`.

```text
task → action → result
```

> A `command` column does **not** mean the agent has shell access. Execution
> remains governed by the command gateway, sandbox, and policy layers
> ([SECURITY.md](SECURITY.md)).

---

## 12. Mutation Transactions

Foundation schema (`anu-safe-mutation.py`):

```sql
CREATE TABLE mutation_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    file_path TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    baseline_ok INTEGER,
    mutation_test_ok INTEGER,
    rollback_attempted INTEGER,
    rollback_verified INTEGER,
    checkpoint_path TEXT,
    details TEXT,
    created_at TEXT NOT NULL
);
```

Safe Mutation v0.2 adds, when missing: `transaction_id`
(`mut-<timestamp>-<8 hex>`, unique index
`idx_mutation_transactions_transaction_id`), `task_id`, `files_changed`,
`baseline_sha256`, `rollback_sha256`, `integrity_verified`, `completed_at`.
The migration is additive, never destructive.

`status` values: `COMMITTED`, `ROLLED_BACK`, `ABORTED_BASELINE_FAILURE`,
`ROLLBACK_VERIFICATION_FAILED`, `MUTATION_ENGINE_ERROR`. `details` holds
gateway results (JSON) for the baseline, mutation, and rollback tests.

```text
baseline → checkpoint → mutation → test → commit OR rollback
```

---

## 13. Checkpoints

```sql
CREATE TABLE checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    phase TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);
```

A checkpoint records a **recovery boundary**:

```text
phase  mutation:<transaction_id>:<PRE_MUTATION | MUTATION | POST_MUTATION | ROLLBACK>
state  CHECKPOINT_CREATED · MUTATION_APPLIED · COMMITTED · ROLLBACK_STARTED · ROLLBACK_VERIFIED
```

Checkpoints are part of ANU's recovery model, not merely filesystem backups.
The files themselves live in `/srv/anu/checkpoints/`.

---

## 14. Test Results

Write contract (`anu-command-gateway.py`):

```sql
INSERT INTO test_results (task_id, test_name, status, output)
VALUES (?, ?, ?, ?);
```

`test_name` is the gateway operation (`python_test`, `python_compile`).
`status` values include `PASS`, `TEST_FAILURE`, `RUNTIME_ERROR`. Every
result is tied to the task that caused it.

---

## 15. Observations

What ANU **learned** about the state of the world, as distinct from
`agent_events`, which record that **something happened**:

```text
test suite contains 18 tests
project currently uses Python 3.13
dependency remains incomplete
rollback restored the original checksum
```

Not yet written by any code. Becomes central in [V0.6.3](versions/v0.6.3.md).
Exact columns to be captured from the live database (section 48).

---

## 16. Errors

Structured failure history: what failed, where, which task, what error,
when.

```text
agent_events   TASK_FAILED              (that it happened)
errors         detailed failure record  (what exactly went wrong)
```

Not yet written by any code. Exact columns to be captured.

---

## 17. Resource Usage

Write contract (`anu-resource-guardian.py`, every 60 s runtime cycle):

```sql
INSERT INTO resource_usage (cpu_percent, memory_mb, temperature_c, disk_free_gb) VALUES (...);
```

- The Guardian answers *"Is it safe to continue now?"*
- `resource_usage` answers *"What resource behaviour has ANU shown over
  time?"*

> **Growth:** one row per minute is about 525,000 rows per year. A retention
> policy is planned for [V0.6.8](versions/v0.6.8.md).

---

## 18. Approvals

Persistence for operations that need explicit human authorization:

```text
task / action → approval request → pending → approved / denied
```

This lets ANU pause without losing its place. **An approval is evidence of
one specific authorization event, not a permanent privilege**, and never a
mechanism for granting permissions automatically. Becomes active in
[V0.6.7](versions/v0.6.7.md).

---

## 19. Research

Knowledge gathered while pursuing a goal, kept separate from raw execution
events:

```text
goal → research task → sources / findings → observation → decision
```

Exact columns to be captured.

---

## 20. Agent Metadata

Durable metadata **about** the agent: identity, runtime information,
version, configuration state. Events describe what the agent **did**.
Exact columns to be captured.

---

## 21. Projects

Verified columns: `id`, `project_key`, `name`, `repository`, `workspace`,
`status` (default `'active'`), `created_at`.

Connects a logical project to its repository and workspace, and is the
parent of goals, commits, and pull requests. Becomes central in
[V0.6.4](versions/v0.6.4.md).

---

## 22. Commits

Connects ANU work to version-control history, giving an eventual trace:

```text
goal → task → mutation → tests → git commit
```

Exact columns to be captured.

---

## 23. Pull Requests

```text
project → commit(s) → pull request → review / merge
```

Important once ANU moves from sandbox projects to real development
workflows. Exact columns to be captured.

---

## 24. Relationship Map

```text
projects ──► goals ──► tasks ─┬──► agent_events
                              ├──► observations
                              ├──► errors
                              ├──► test_results
                              ├──► checkpoints
                              ├──► mutation_transactions
                              ├──► actions
                              ├──► task_execution_specs
                              ├──► task_dependencies
                              └──► child tasks

projects ──► commits ──► pull_requests
```

---

## 25. Goal, Task, Execution

| Concept | Question |
|---|---|
| Goal | What outcome are we trying to achieve? |
| Task | What unit of work should be completed? |
| Execution specification | How should that task be executed? |

V0.5.9 showed why these boundaries matter. Task 186 was blocked because its
relative `changes_file` was resolved against the runtime working directory
instead of the project. The fix:

```text
project → resolve project → resolve changes file inside project
```

---

## 26. Event Attribution

Events carry `goal_id`, `task_id`, `parent_task_id`, `run_id`, and `attempt`
whenever context exists. That answers: which goal, which task, which run,
and which retry attempt produced this event.

---

## 27. Run Identity

A `run_id` identifies one execution instance, e.g.
`run-20261003T080645370634Z-97507961`.

```text
task
 ├── run A / attempt 1
 ├── run B / attempt 2
 └── run C / attempt 3
```

---

## 28. Brain Versus Logs

| Brain | Logs |
|---|---|
| Structured durable state, relationships, lifecycle, audit records, recoverable state | Diagnostic stream, runtime messages, debug context, human-readable output |

**A log line must never be the only place a critical state transition
exists.**

---

## 29. Brain Versus Filesystem

The filesystem stores artifacts: source files, `changes.json`, checkpoints,
project files, logs, configuration. The Brain stores information **about**
them:

```text
filesystem  /srv/anu/checkpoints/mutations/...
brain       checkpoint record, transaction record, task_id, phase, state
```

So ANU can reason about work without loading the filesystem.

---

## 30. Foreign-Key Policy

The schema declares foreign keys (`tasks.goal_id`, `tasks.parent_task_id`,
`goals.project_id`, `task_dependencies.*`, `task_execution_specs.task_id`,
`checkpoints.task_id`). SQLite only enforces them on connections that turn
them on, and the default is off: a direct CLI connection shows
`PRAGMA foreign_keys` = `0`.

**Current state:** only `anu-task-graph-v0.1.py` (and the tests) run
`PRAGMA foreign_keys = ON`. The Task Engine, Safe Mutation, command gateway,
and Resource Guardian connect without it. On those connections, foreign keys
and `ON DELETE CASCADE` are not enforced.

> **Rule: every ANU SQLite application connection must enable
> `PRAGMA foreign_keys = ON`**, through one shared connection helper rather
> than callers remembering it.

Before turning enforcement on everywhere, check existing data for orphans
(`PRAGMA foreign_key_check;`). Rows written without enforcement may already
violate the declared keys.

---

## 31. Schema Migration Policy

```text
PREFERRED:  migration → verify → regression → freeze
AVOID:      DROP TABLE · blind rebuild · lost history
```

The Brain Schema v0.2 migration is the model: existing `agent_events` +
new attribution columns + indexes, metadata untouched.

Always take a backup first, using the SQLite backup API
([OPERATIONS.md](OPERATIONS.md)).

---

## 32. Backward Compatibility

Old records stay readable. An event with `task_id`, `goal_id`, and `run_id`
all `NULL` is still valid; new records carry full attribution. The Brain
evolves without invalidating history.

---

## 33. Current Brain Responsibilities

Goals, tasks, dependencies, execution specs, event history, actions,
observations, errors, resource usage, approvals, checkpoints, mutation
transactions, test results, projects, research, commits, pull requests, agent
metadata.

Of these, the tables **actively written today** are `tasks`,
`task_dependencies`, `task_execution_specs`, `agent_events`, `checkpoints`,
`mutation_transactions`, `test_results`, and `resource_usage`. The rest
exist and are waiting for the versions that use them. This is already enough
for the current autonomous control loop.

---

## 34. V0.6.1 Model Intelligence Extension (future)

Model use gets its own table instead of being mixed into task tables:

```text
model_requests
  id, task_id, goal_id, run_id, provider, model, status,
  input_tokens, output_tokens, estimated_cost, latency_ms,
  privacy_class, created_at, completed_at, error
```

It answers which model was used, what it cost, how many tokens were used,
which task caused the request, whether it failed, and whether it was
local or API.

> `model_requests` is for **observability**. The **authoritative** budget
> ledger lives in the model gateway, because the agent can write the Brain
> and must not be able to reset its own spending
> ([MODEL-STRATEGY.md](MODEL-STRATEGY.md) section 5.1).

This is a future design, not a claim that `model_requests` exists.

---

## 35. Model Context Is Not Permanent Brain Data

Do not store complete prompts and responses. They may contain source code,
research, private context, and task details.

```text
Brain → relevant structured context → Model Gateway
```

Store the metadata, the decision, a summary, the cost, and the status. Never
store unlimited raw prompts. That limits storage, privacy exposure, and
database growth.

---

## 36. Future Memory Model

```text
               ANU BRAIN
       ┌───────────┼───────────┐
  Operational   Knowledge    History
    Memory       Memory      Memory
  (tasks …)    (research)   (events)
```

> **Do not add a generic "memory blob" merely because the model needs more
> context.** Memory keeps semantic structure.

---

## 37. Query Patterns

```sql
SELECT * FROM goals                 WHERE id = ?;
SELECT * FROM tasks                 WHERE id = ?;
SELECT * FROM agent_events          WHERE task_id = ? ORDER BY id ASC;   -- history
SELECT * FROM test_results          WHERE task_id = ? ORDER BY id ASC;   -- tests
SELECT * FROM checkpoints           WHERE task_id = ? ORDER BY id ASC;   -- recovery points
SELECT * FROM mutation_transactions WHERE task_id = ? ORDER BY id DESC;  -- mutations
```

Read-only inspection on the Pi:

```bash
sudo -u anu-agent sqlite3 -readonly /srv/anu/brain/anu-brain.db \
  "SELECT id, status, attempts FROM tasks ORDER BY id DESC LIMIT 10;"
```

---

## 38. Example Complete Task History

```text
GOAL
 └── TASK
      ├── TASK_CREATED
      ├── TASK_STATE planning → ready → running
      ├── CHECKPOINT (PRE_MUTATION: CHECKPOINT_CREATED)
      ├── MUTATION_TRANSACTION (COMMITTED)
      ├── TEST_RESULT (PASS)
      ├── OBSERVATION            (from V0.6.3)
      └── TASK_COMPLETED
```

A durable narrative of execution.

---

## 39. Example Failure and Recovery

```text
TASK
 ├── RUNNING
 ├── CHECKPOINT_CREATED
 ├── MUTATION_APPLIED
 ├── TEST_FAILURE
 ├── ROLLBACK_STARTED
 ├── ROLLBACK_VERIFIED
 └── task status: failed (retryable) or blocked (attempts exhausted)
```

This is why the Brain is more than a task table: it records what recovery
and audit need.

---

## 40. Brain Integrity Rules

1. A task has a stable integer database identity.
2. `goal_id` in SQLite is the database goal identity.
3. Agent-layer goal identifiers are normalized explicitly at the adapter
   boundary (prevents the V0.5.6 / V0.5.9 integer-vs-string bug class).
4. Task dependencies stay acyclic.
5. Execution specifications stay attached to real tasks.
6. Important mutations have durable transaction and checkpoint history.
7. Test results stay attached to the task that caused them.
8. Events are attributed to task, run, and attempt whenever context exists.
9. Schema migrations preserve historical data.
10. **Unknown database state is never silently interpreted as successful
    work.**

---

## 41. Database Backup

The Brain is critical state, so backups are part of operational safety.
Manual snapshots in `brain/` include:

```text
anu-brain.db.backup-v0.2-20261003T080022Z
anu-brain.db.pre-task-graph-….bak
anu-brain.db.v0.5.9-live-before-cycle
anu-brain.db.v0.5.9-autonomous-before-task
```

Take backups with the SQLite backup API, not `cp`, while the service runs
([OPERATIONS.md](OPERATIONS.md)). A backup is sensitive operational data
([SECURITY.md](SECURITY.md) section 8.4).

---

## 42. Public Repository Rule

The database file is **never** committed: it holds operational, task, and
failure history, project information, possibly sensitive metadata, and,
later, model-use information. `.gitignore` excludes `brain/` and `*.db`,
`*.db-wal`, `*.db-shm`, `*.sqlite`.

The repository holds schema definitions, migrations, documentation, tests,
and sample fixtures, not the production Brain.

---

## 43. Canonical Source of Truth

The runtime database is `/srv/anu/brain/anu-brain.db`. The canonical schema
definition will be version-controlled migration files:

```text
/srv/anu/
├── migrations/                      versioned in git, root-owned, read-only to the service
│   ├── 0001_initial.sql             baseline captured from the live database
│   ├── 0002_agent_event_context.sql
│   ├── 0003_task_dependencies.sql
│   ├── 0004_task_execution_specs.sql
│   └── ...
└── brain/
    └── anu-brain.db                 runtime state, never versioned
```

Migrations must **not** live inside `brain/`. That directory is
git-ignored, so they would never be versioned. It is also writable by the
agent, which would let ANU edit its own schema history.

The migration runner should record applied migrations in the database
(e.g. a `schema_migrations` table, or `PRAGMA user_version`).

---

## 44. Schema Ownership

| Subsystem | Owns |
|---|---|
| Task Engine | `tasks` |
| Task Graph | `task_dependencies` |
| Execution Spec | `task_execution_specs` |
| Agent/Event Context | `agent_events` attribution |
| Safe Mutation | `mutation_transactions`, mutation checkpoint records |
| Command Gateway / Sandbox | `test_results` |
| Resource Guardian | `resource_usage` |
| Model Gateway (V0.6.1) | `model_requests` |

No single module becomes responsible for unrelated structures.

---

## 45. V0.6.1 Schema Direction

```text
goal → task → run → model request → decision → action → observation → result
```

The long-term trace:

| Question | Answered by |
|---|---|
| Why? | goal |
| What? | task |
| How? | execution specification |
| Who / when? | run + attempt |
| With what intelligence? | model request |
| What happened? | events / actions |
| What did we learn? | observations |
| What changed? | mutation transaction |
| Was it correct? | test result |
| Can we recover? | checkpoint |
| What was the final result? | task state |

That is the core information model of ANU.

---

## 46. Brain Architecture

```text
                       USER
                         │
                       GOAL
                         │
                       TASK
          ┌──────────────┼──────────────┐
    DEPENDENCIES     MODEL USE     EXECUTION SPEC
          └──────────────┼──────────────┘
                   AGENT RUNTIME
          ┌──────────────┼──────────────┐
       EVENTS       OBSERVATIONS      ERRORS
          │
   CONTROLLED ACTION
          │
  SAFE MUTATION / SANDBOX
     ┌────┼─────┐
CHECKPOINT TEST RESULT
     └────┼─────┘
   FINAL TASK STATE
```

Everything important eventually comes back into the Brain.

---

## 47. Final Definition

The Brain is not *"everything ANU has ever seen."* It is:

> **the smallest durable state system that allows ANU to understand what it
> is trying to accomplish, what it has done, what happened, what it learned,
> and what it should safely do next.**

```text
GOAL → TASK → DEPENDENCY → EXECUTION → OBSERVATION → VERIFICATION → RECOVERY → RESULT
```

The Brain exists to make that chain persistent.

---

## 48. Schema Status

| Status | Tables |
|---|---|
| **Full DDL verified** | `goals`, `tasks`, `task_dependencies`, `task_execution_specs`, `checkpoints`, `mutation_transactions` |
| **Columns verified** (inspection or code write contract) | `agent_events`, `actions`, `projects`, `test_results`, `resource_usage` |
| **Present, columns not yet captured** | `agent_metadata`, `approvals`, `errors`, `observations`, `pull_requests`, `research`, `commits` |
| **Planned** | `model_requests` (V0.6.1) |

**Do not invent missing columns to make the documentation look complete.**

### Capturing the baseline

Run on the Pi. It writes a file that can be copied to the workspace as
`migrations/0001_initial.sql`:

```bash
sudo -u anu-agent sqlite3 -readonly /srv/anu/brain/anu-brain.db .schema > /tmp/anu-schema.sql
sudo -u anu-agent sqlite3 -readonly /srv/anu/brain/anu-brain.db <<'SQL' > /tmp/anu-schema-detail.txt
.indexes
SELECT m.name, p.* FROM sqlite_master m, pragma_table_info(m.name) p WHERE m.type='table' ORDER BY m.name, p.cid;
SELECT m.name, f.* FROM sqlite_master m, pragma_foreign_key_list(m.name) f WHERE m.type='table';
PRAGMA journal_mode;
PRAGMA foreign_key_check;
SQL
```

The next schema freeze includes this full dump for every application table,
followed by a version-controlled migration baseline.

---

## Known Issue

Several tests write to this live database ([TESTING.md](TESTING.md)), so
test records and real work share the same tables. Back up the Brain before
running the full suite.
