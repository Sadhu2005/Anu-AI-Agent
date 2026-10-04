# ANU Design Philosophy: Time-rich, Compute-constrained

## The Idea

ANU is intentionally designed to run on a **Raspberry Pi 5** (4 cores, 8 GB
RAM) rather than requiring a powerful server or GPU.

The Pi is not built for heavy AI computation, so ANU does not depend on doing
large amounts of computation quickly. The design assumes ANU has plenty of time
to complete its work.

> **ANU is time-rich, but compute-constrained.**

Instead of trying to make the Raspberry Pi behave like a high-end AI server,
the agent works **slowly, safely, and continuously**.

## What This Means in Practice

ANU can:

- execute one task at a time
- wait when resources are unavailable
- retry failed work later
- pause when CPU, RAM, disk, or temperature conditions are not suitable
- continue long-running work over many hours
- save its state so it can continue after a restart

That is why **resource governance is a first-class part of the architecture**.
The Pi checks its own health before allowing any autonomous work:

```text
Health Check → Resource Admission → Scheduler → Agent → Execution
```

If the machine is healthy, ANU continues. If resources become constrained, ANU
does not force the task to finish. It waits.

```text
RAM low               → pause
Temperature high      → pause
Disk too low          → deny
Another task running  → wait
```

This is fundamentally different from an agent that assumes unlimited compute.

## How the Philosophy Is Enforced

Every behaviour above is backed by configuration or code:

| Behaviour | Enforced by |
|---|---|
| Health check before every scheduler tick | `bin/anu-runtime.py` runs the scheduler only when the Guardian reports `NORMAL` (V0.5.9) |
| Stop starting new work when warm | Guardian `WARNING` at **60 °C** (`config/anu-resources.yaml`) |
| Pause when hot | Guardian / Governor `PAUSE` at **65 °C** |
| Pause when RAM is critically low | Guardian `PAUSE` below **1024 MB** free (`emergency_free_mb`) |
| Deny workloads that would break the RAM reserve | Governor keeps **2048 MB** free (`config/anu-admission-policy.yaml`) |
| Deny / pause when disk is low | **50 GB** minimum free disk (both files) |
| One task at a time | `max_parallel_tasks: 1`; scheduler `SCHEDULER_MAX_TASKS = 1` and a lock file |
| Bounded ANU budget | 3072 MB RAM, 50 % CPU |
| Retry later, not endlessly | Maximum **3 attempts** per task; `blocked` tasks are never auto-retried |
| Wait and resume | WAITING state and re-wake (V0.5.7) |
| Survive restarts | SQLite brain + checkpoints; full recovery planned in V0.6.0 / V0.6.8 |

### Why these numbers suit the Pi 5

- **60 °C / 65 °C**: the Pi 5 firmware starts throttling around 80 °C. Stopping
  new work at 60 °C and pausing at 65 °C keeps a wide safety margin, and leaves
  thermal headroom for the other production services on the same Pi, which
  always have priority over ANU. ANU idles at about 46 °C.
- **3 attempts**: each attempt costs a checkpoint, a mutation, and a Docker
  sandbox test run (up to 512 MB RAM and 120 s). A retry must bring a *new*
  mutation plan, so a fourth or fifth identical-cost attempt rarely helps.
  Being time-rich, ANU should instead classify the failure and come back later
  (V0.6.0), not hammer the same task.
- **50 GB free disk**: the Pi has about 437 GB free; reserving 50 GB protects
  the host while leaving plenty for ANU's 30 GB workspace limit.

## ANU Limits Itself Deliberately

Beyond resources, ANU's own capabilities are restricted (`config/anu-policy.yaml`):

- **Filesystem:** writes only inside `/srv/anu` (`home`, `brain`, `workspace`,
  `sandbox`, `browser`, `logs`, `runtime`, `checkpoints`, `secrets`, `tests`);
  `config/`, `src/`, `bin/` are read-only; system paths such as `/etc`, `/usr`,
  `/home`, `/var` are forbidden.
- **No unrestricted shell:** only named, allow-listed commands through the
  command gateway.
- **No direct Docker access:** `docker.enabled: false` and
  `allow_docker_socket: false` mean ANU cannot run Docker itself. Code is still
  executed inside Docker containers, but **only** through the root
  `anu-sandboxd` service, using the `python-sandbox` profile (no network,
  512 MB, 120 s).
- **No network, browser, downloads, or package installs** at this stage.
- **No git push, deployment, or production access.**
- **No sudo or system changes.**
- **Every code mutation** is checkpointed and can be rolled back.

## The Pi Is Not a Weakness

The Raspberry Pi is not a limitation to hide. It is part of the design.

ANU does not need to be fast to be useful. It needs to be:

> **safe, persistent, resource-aware, recoverable, and eventually autonomous.**

A powerful server might finish a task in seconds. ANU may take minutes or hours,
but it keeps working in the background, saves its state, waits when necessary,
recovers from failures, and continues later.

So the optimisation target is **not**:

> "How fast can ANU finish?"

It is:

> **"How reliably can ANU keep making safe progress with limited resources?"**

## No Large Local Model (Yet)

ANU does not depend on a large AI model running continuously on the Pi. The
resource policy explicitly keeps models disabled at this stage
(`models.enabled: false`, with future caps of 3 GB normal and 5 GB with approval).

The Pi provides the **runtime, scheduler, controller, safety system, storage,
execution environment, and persistent brain**.

That is why the architecture can scale later: the intelligence / model layer
can become smarter (a local model, a remote API, or both, decided in V0.6.1)
**without redesigning the safe execution foundation underneath it**.

## See Also

- [WHY.md](WHY.md): why ANU is built safety-first
- [ARCHITECTURE.md](ARCHITECTURE.md): execution chain and resource policy
- [versions/v0.6.6.md](versions/v0.6.6.md): resource-aware intelligence
