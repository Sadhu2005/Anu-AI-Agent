# ANU: Fresh Raspberry Pi Setup

> **Status: V0.5.9 reconstruction runbook.** Every value below was checked
> against the live Pi, but the full sequence has **not** yet been reproduced on
> a blank machine. Gaps are listed in [section 21](#21-known-gaps).

The target is the same architecture as the current deployment:

```text
Raspberry Pi 5 → Debian 13 → Docker → anu-sandbox.service (root)
                                     → anu-agent.slice → anu-agent.service
                                       → anu-runtime.py → Guardian → scheduler
                                       → AgentController → Task Engine
                                       → Safe Mutation → SQLite brain
```

ANU is **time-rich and compute-constrained**. The Pi does not run heavy AI
computation; it makes safe progress over time within resource, filesystem,
sandbox, and execution limits.

> **Rebuild rule:** do not reconstruct ANU by copying random files from an old
> `/srv/anu`. Use the git repository, the versioned config, and the versioned
> systemd units in `deploy/systemd/`. Backup files are history, not an
> installation mechanism.

---

## 1. Reference Hardware

| | |
|---|---|
| Board | Raspberry Pi 5 Model B |
| RAM / CPU | 8 GB / 4 cores |
| Storage | ~469 GB root filesystem (512 GB card) |
| Swap | 2 GB |
| Architecture | aarch64 |
| OS | Debian GNU/Linux 13 (trixie), Raspberry Pi kernel |

Capacity needed: workspace (≤ 30 GB), brain, Docker images, checkpoints, logs,
plus the 50 GB free-disk reserve that the Guardian enforces.

## 2. Operating System

Install 64-bit Raspberry Pi OS / Debian 13. After first boot:

```bash
cat /etc/os-release            # Debian 13 (trixie)
uname -m                       # aarch64
cat /proc/device-tree/model    # Raspberry Pi 5 Model B
free -h && df -h
command -v vcgencmd            # temperature source used by the Guardian
```

Do not continue until the base system is healthy.

## 3. Base Packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3 python3-yaml sqlite3 curl ca-certificates
```

| Package | Why |
|---|---|
| `python3` | All components run on the **system** interpreter `/usr/bin/python3` (Python 3.13 on the reference Pi) |
| `python3-yaml` | **The only third-party Python dependency** (PyYAML), used by the Guardian, Governor, gateway, executor, execution profiles, and sandbox daemon |
| `sqlite3` | CLI for inspecting the brain |
| `git` | Source and deployment |

No virtual environment is needed: the systemd units call `/usr/bin/python3`
directly, so packages must be visible to the system interpreter. Everything
else is the Python standard library.

```bash
python3 -c "import yaml, sqlite3; print('OK', yaml.__version__)"
```

## 4. The `anu-agent` User: Exact IDs Required

> **Critical:** `bin/anu-sandboxd.py` hard-codes `AUTHORIZED_UID = 999` and
> `AUTHORIZED_GID = 984`, and `config/anu-sandbox.yaml` runs containers as
> `user: 999:984`. The daemon refuses to start (`Sandbox UID/GID policy
> mismatch`) unless these agree, and only GID 984 may use the sandbox socket.
> `anu-agent` must therefore be **uid 999, gid 984**.

Check the IDs are free, then create the account:

```bash
getent passwd 999 || echo "uid 999 free"
getent group 984  || echo "gid 984 free"

sudo groupadd --system --gid 984 anu-agent
sudo useradd  --system --uid 999 --gid 984 \
  --home-dir /srv/anu/home --no-create-home \
  --shell /usr/sbin/nologin anu-agent

id anu-agent   # uid=999(anu-agent) gid=984(anu-agent) groups=984(anu-agent)
```

If 999 or 984 are taken on the new machine, change **all three** places
together (`anu-sandboxd.py`, `anu-sandbox.yaml`, and the user) and record it
in [DECISIONS.md](DECISIONS.md).

Rules:

- No sudo rights for `anu-agent`.
- **Never** add `anu-agent` to the `docker` group (see section 7).

## 5. Source and Directory Layout

Clone the canonical repository as root:

```bash
sudo git clone https://github.com/Sadhu2005/Anu-AI-Agent.git /srv/anu
cd /srv/anu
sudo git checkout v0.5.9-integration     # or the latest frozen tag
```

Create the runtime directories (not in git):

```bash
sudo mkdir -p /srv/anu/{home,brain,workspace/projects,sandbox,browser,logs,runtime,secrets,checkpoints/mutations,src}
```

Set ownership and modes to match the reference Pi:

```bash
# code and config: root-owned, readable, never writable by ANU
sudo chown -R root:root /srv/anu/bin /srv/anu/config /srv/anu/src /srv/anu/docs /srv/anu/deploy
sudo chmod 755 /srv/anu /srv/anu/bin /srv/anu/config /srv/anu/src

# ANU state: private to anu-agent
sudo chown -R anu-agent:anu-agent \
  /srv/anu/{home,brain,workspace,sandbox,browser,logs,runtime,secrets,tests,checkpoints}
sudo chmod 700 /srv/anu/{home,brain,workspace,sandbox,browser,logs,runtime,secrets,tests}
sudo chmod 750 /srv/anu/checkpoints

# repository metadata stays root-owned (git runs as root; see WORKFLOW.md)
sudo chown -R root:root /srv/anu/.git /srv/anu/.gitignore
```

> The reference Pi currently has `.git` owned by `anu-agent`. That is
> changed to root during workflow adoption ([WORKFLOW.md](WORKFLOW.md)
> section 25): ANU must not be able to change its own code or history, and
> root must not run git hooks writable by another user.

| Path | Owner | Mode | Service access |
|---|---|---|---|
| `/srv/anu` | root | 755 | read-only (ProtectSystem=strict) |
| `bin/`, `config/`, `src/` | root | 755 | `ReadOnlyPaths` |
| `home/`, `brain/`, `workspace/`, `sandbox/`, `browser/`, `logs/`, `runtime/`, `secrets/`, `tests/` | anu-agent | 700 | `ReadWritePaths` |
| `checkpoints/` | anu-agent | 750 | `ReadWritePaths` (drop-in, V0.5.9) |

## 6. Docker (Official Repository)

The reference Pi uses **Docker's official apt repository** (`docker-ce`,
Docker 29.8.1, Compose v5.5.1), not Debian's `docker.io`.

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
docker --version && docker compose version
```

Pre-pull the pinned sandbox image (the digest in `config/anu-sandbox.yaml`;
it is `python:3.13-slim` for arm64):

```bash
sudo docker pull python@sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81
```

## 7. The Docker Security Boundary

```text
anu-agent ──(unix socket, gid 984 only)──► anu-sandboxd (root) ──► Docker ──► restricted container
```

- `anu-agent` has **no** Docker access. Docker-group membership is effectively
  root on the host and would bypass every ANU boundary.
- "Docker disabled" in `anu-policy.yaml` means ANU cannot control Docker
  directly. It does not mean Docker is absent.
- `anu-sandboxd` creates `/run/anu-sandbox/` (root:984, mode 750) and
  `sandbox.sock` (root:984, mode 660). The agent service talks to it over
  `AF_UNIX` only; it has `PrivateNetwork=yes`.

Verify:

```bash
id anu-agent | grep -q docker && echo "FAIL: anu-agent in docker group" || echo "PASS"
```

## 8. Configuration

All nine files come from the repository (`/srv/anu/config`). Do not edit
values independently in several files; see
[CONFIGURATION.md](CONFIGURATION.md).

```text
anu-runtime.yaml            anu-resources.yaml          anu-admission-policy.yaml
anu-command-gateway.yaml    anu-execution-profiles.yaml anu-sandbox.yaml
anu-executor.yaml           anu-projects.yaml           anu-policy.yaml
```

The V0.5.9 alignment (warning 60 °C, pause 65 °C, 50 GB disk, 3 attempts,
checkpoints writable, Docker wording) is in the repository. Confirm before
going live:

```bash
grep -E 'warning_celsius|pause_celsius|minimum_free_gb' /srv/anu/config/anu-resources.yaml
grep -E 'warning_temperature_c|pause_temperature_c|minimum_free_disk_gb' /srv/anu/config/anu-admission-policy.yaml
```

## 9. systemd Units

Versioned copies of the live units are in `deploy/systemd/` (byte-identical to
the reference Pi):

| File | Purpose |
|---|---|
| `anu-sandbox.service` | Root sandbox daemon; requires `docker.service`; no network; writes only `/run/anu-sandbox` |
| `anu-agent.slice` | **Resource ceiling for ANU:** `CPUQuota=200%` (≈ 2 cores), `MemoryHigh=2560M`, `MemoryMax=3072M`, `TasksMax=64`, `IOWeight=50` |
| `anu-agent.service` | The runtime as `anu-agent`; `Requires=anu-agent.slice`; `WorkingDirectory=/srv/anu/home`; `ProtectSystem=strict`, `PrivateNetwork=yes`, no capabilities, `OOMScoreAdjust=500` |
| `anu-agent.service.d/v0.5.9-checkpoints.conf` | `ReadWritePaths=/srv/anu/checkpoints` |

Install:

```bash
sudo cp /srv/anu/deploy/systemd/anu-sandbox.service /etc/systemd/system/
sudo cp /srv/anu/deploy/systemd/anu-agent.slice     /etc/systemd/system/
sudo cp /srv/anu/deploy/systemd/anu-agent.service   /etc/systemd/system/
sudo mkdir -p /etc/systemd/system/anu-agent.service.d
sudo cp /srv/anu/deploy/systemd/anu-agent.service.d/v0.5.9-checkpoints.conf \
        /etc/systemd/system/anu-agent.service.d/
sudo systemctl daemon-reload
```

Start the sandbox first, and the agent only after the brain exists (section 10):

```bash
sudo systemctl enable --now anu-sandbox.service
sudo systemctl status anu-sandbox.service --no-pager
ls -l /run/anu-sandbox/sandbox.sock     # srw-rw---- root anu-agent
```

The slice is the **operating-system** enforcement of the ANU budget in
`anu-resources.yaml` (3072 MB, ~50 % of 4 cores, low priority). Keep them in
step.

## 10. The SQLite Brain

> **Gap:** the repository does not yet contain a schema bootstrap. The core
> tables (`tasks`, `goals`, `agent_events`, `checkpoints`, `resource_usage`,
> `test_results`, `projects`, `observations`, `research`, `errors`, `actions`,
> `approvals`, `agent_metadata`, `commits`, `pull_requests`) were created by an
> initial schema that is not in git. Do not recreate them from memory.

Until a bootstrap exists, choose one:

- **Restore** a backup of `anu-brain.db` from the old Pi (made with the SQLite
  backup API; see [OPERATIONS.md](OPERATIONS.md)), or
- **Recreate from the captured schema**: `migrations/0001_initial.sql`
  (captured 15 Oct 2026; [DATABASE.md](DATABASE.md) sections 43 and 48).
  On the new Pi:

```bash
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db < /srv/anu/migrations/0001_initial.sql
```

Components add their own tables on first use (`task_dependencies`,
`task_execution_specs`, extra `mutation_transactions` columns).

Verify:

```bash
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db ".tables"
sudo -u anu-agent test -w /srv/anu/brain/anu-brain.db && echo "PASS brain writable"
```

## 11. Start the Agent

```bash
sudo systemctl enable --now anu-agent.service
sudo systemctl show anu-agent.service -p ActiveState -p SubState -p User -p WorkingDirectory -p Slice
# ActiveState=active SubState=running User=anu-agent WorkingDirectory=/srv/anu/home Slice=anu-agent.slice
sudo tail -f /srv/anu/logs/anu.log
```

Healthy, with no tasks:

```text
[ANU] Health check: status=NORMAL temp=46.3C memory_available=7368MB
[ANU] Scheduler cycle completed: results=0
```

The scheduler runs **inside** the runtime (one tick per 60 s cycle, only when
the Guardian reports `NORMAL`, at most 1 task, `runtime/anu-scheduler.lock`).
No separate scheduler service or timer is needed ([ADR-002](DECISIONS.md)).

## 12. Verify Each Boundary

```bash
# Guardian
sudo -u anu-agent python3 -B /srv/anu/bin/anu-resource-guardian.py | grep '"status"'

# checkpoint write inside the service namespace (not just file permissions)
sudo systemd-run --wait --pipe -p User=anu-agent -p ProtectSystem=strict \
  -p ReadWritePaths=/srv/anu/checkpoints \
  touch /srv/anu/checkpoints/mutations/.probe && echo "PASS checkpoint write"
sudo rm -f /srv/anu/checkpoints/mutations/.probe

# slice limits applied
systemctl show anu-agent.slice -p MemoryMax -p CPUQuotaPerSecUSec -p TasksMax
```

## 13. Regression Suite

Service status is not acceptance. Run the full suite as `anu-agent`
([TESTING.md](TESTING.md)). Several tests write to the live brain, so run them
**before** real work exists, or with the service stopped and the brain backed up.

## 14. First Autonomous Task

Use a dedicated sandbox project, never a real one:

```text
/srv/anu/workspace/projects/sandbox-test-project/
├── app.py
├── tests/test_app.py
└── .anu-v0.5.9-changes.json      [{"file": "app.py", "old": "...", "new": "..."}]
```

Execution specification: `project=sandbox-test-project`,
`changes_file=.anu-v0.5.9-changes.json`, `operation=python_test`.

Prove the success path:

```text
task created → scheduler discovers it → agent → controller → Task Engine
→ baseline test PASS → checkpoint → mutation → sandbox test PASS
→ COMMITTED → transaction in brain → task COMPLETE
```

Then a deliberately failing change:

```text
checkpoint → mutation → test FAIL → rollback → hashes match baseline
→ test PASS again → ROLLED_BACK
```

## 15. Acceptance Gate

A fresh Pi is an ANU runtime only when every item is demonstrated:

```text
[ ] Debian 13, aarch64, Raspberry Pi 5 confirmed
[ ] python3-yaml importable by /usr/bin/python3
[ ] anu-agent exists with uid 999 / gid 984, not in docker group
[ ] /srv/anu layout, ownership and modes match section 5
[ ] Docker (docker-ce) active; pinned sandbox image present
[ ] anu-sandbox.service active; socket root:984 0660
[ ] sandbox network isolation verified
[ ] configuration from repository; alignment checks pass
[ ] anu-agent.slice limits applied
[ ] brain initialised and writable
[ ] Guardian reports NORMAL
[ ] anu-agent.service active, User=anu-agent, WorkingDirectory=/srv/anu/home
[ ] scheduler ticks inside runtime; lock works
[ ] checkpoint write succeeds inside the service namespace
[ ] regression suite passes
[ ] autonomous sandbox task COMMITTED and COMPLETE
[ ] rollback test ROLLED_BACK with integrity verified
[ ] brain records the transactions; logs are produced
```

## 16. Models

Model execution stays disabled (`models.enabled: false`). The Pi provides
runtime, scheduling, governance, sandboxing, Safe Mutation, persistence, and
recovery; see [ADR-004](DECISIONS.md).

## 17. Not Part of ANU

The reference Pi also runs, independently of ANU:

- a portfolio website stack in Docker (nginx on ports 80/443, web, API, Postgres)
- `cloudflared` (from its own apt repository), listening on `127.0.0.1:20241`

Neither is required to rebuild ANU. ANU's resource policy gives them priority.

## 18. Security Hierarchy to Preserve

```text
ANU intelligence (future)
  → AgentController → Scheduler → Resource Admission → Task Engine
  → Safe Mutation ─┬─ Command Gateway → anu-sandboxd → Docker → isolated process
                   └─ Checkpoint system → Recovery
```

Operating-system layers underneath: `anu-agent` user (no sudo, no docker
group) → `anu-agent.slice` (CPU, RAM, tasks) → `anu-agent.service` sandbox
(read-only system, private network, no capabilities).

## 19. Updating an Existing Pi

```bash
PIGIT="sudo git -c safe.directory=/srv/anu -C /srv/anu"
$PIGIT fetch origin --tags
$PIGIT checkout <tag>
sudo cp /srv/anu/deploy/systemd/... /etc/systemd/system/...   # only if units changed
sudo systemctl daemon-reload
sudo systemctl restart anu-sandbox.service anu-agent.service
```

Full procedure, including verification and rollback: [WORKFLOW.md](WORKFLOW.md).

## 20. Operating Model

```text
safe → bounded → persistent → patient → recoverable → autonomous
```

The objective is not maximum speed; it is **reliable autonomous progress over
time on limited hardware**.

## 21. Known Gaps

| # | Gap | Status |
|---|---|---|
| 1 | Pre-git setup history | Partly reconstructed in [HISTORY.md](HISTORY.md) |
| 2 | Third-party Python dependencies | **Resolved:** only PyYAML (`python3-yaml`) |
| 3 | Exact `anu-sandbox.service` | **Resolved:** versioned in `deploy/systemd/` |
| 4 | Docker installation source | **Resolved:** official repo, `docker-ce` |
| 5–7 | Unified resource policy, retry and temperature semantics | **Aligned** in the repository (V0.5.9); not yet deployed to the Pi; single source of truth in V0.6.6 |
| 8 | Checkpoint path in policy | **Resolved** in `anu-policy.yaml` |
| 9 | Docker capability wording | **Resolved** in config comment and docs |
| 10 | Canonical repository | **Resolved:** `github.com/Sadhu2005/Anu-AI-Agent` |
| 11 | Brain schema bootstrap | Baseline captured in `migrations/0001_initial.sql`. **Open:** a migration runner ([DATABASE.md](DATABASE.md) section 43) |
| 12 | Hard-coded uid 999 / gid 984 | **Open:** move to configuration |
| 13 | Hard-coded `/srv/anu` paths | **Open:** blocks testing anywhere else |
| 14 | `sandbox-test-project` contents | **Open:** not in git; add as a fixture |
| 15 | Secrets procedure | **Open:** `secrets/` exists but is not documented (to be covered in `SECURITY.md`) |
| 16 | Full rebuild on a blank Pi | **Open:** this runbook has not been executed end-to-end |
