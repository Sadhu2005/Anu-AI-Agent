# ANU Configuration

All configuration lives in `config/` (deployed to `/srv/anu/config`, read-only
for ANU). Values here are the versions in git. The Pi may differ until the
next deployment.

Runtime paths on device default to **`ANU_BASE=/srv/anu`** when set in the
environment; several V0.6.1b+ modules (`anu-db-v0.1.py`, goal services,
capability registry, sandboxd) read it for Brain and workspace locations.

## Overview

| File | Read by | Purpose |
|---|---|---|
| `anu-runtime.yaml` | runtime components | Paths: brain, logs, runtime files, workspace, sandbox |
| `anu-resources.yaml` | `anu-resource-guardian.py` | Host health thresholds → NORMAL / WARNING / PAUSE |
| `anu-admission-policy.yaml` | `anu-resource-governor.py` | Per-workload admission, file and storage limits |
| `anu-command-gateway.yaml` | `anu-command-gateway.py` | Allow-listed commands and their resources |
| `anu-execution-profiles.yaml` | `anu-execution-profile.py` | Resource envelope per command |
| `anu-sandbox.yaml` | `anu-sandboxd.py` | Docker sandbox hardening and operations |
| `anu-executor.yaml` | `anu-executor.py` | Workspace file operations and limits |
| `anu-projects.yaml` | registration | Registered projects and project rules |
| `anu-policy.yaml` | **no code yet** | Declarative identity, filesystem, capability, and limits policy |

## `anu-resources.yaml` (Guardian)

| Key | Value | Effect |
|---|---|---|
| `temperature.warning_celsius` | 60 | `WARNING`, so the runtime stops scheduling new work |
| `temperature.pause_celsius` | 65 | `PAUSE` |
| `temperature.resume_celsius` | 55 | Not used by code yet (hysteresis planned in V0.6.6) |
| `memory.emergency_free_mb` | 1024 | Below this free RAM: `PAUSE` |
| `memory.max_mb` | 3072 | ANU RAM budget |
| `storage.minimum_free_gb` | 50 | Below this free disk: `PAUSE` |
| `cpu.max_percent` | 50 | ANU CPU budget |
| `agent.max_parallel_tasks` | 1 | |
| `production.priority` / `agent.priority` | highest / low | Other services on the Pi come first |

## `anu-admission-policy.yaml` (Governor)

| Section | Key values |
|---|---|
| `host_safety` | Min free RAM 2048 MB, min free disk 50 GB, warning 60 °C, pause 65 °C |
| `anu_budget` | RAM 3072 MB, CPU 50 %, 1 parallel task |
| `storage` | Workspace max 30 GB, project max 15 GB |
| `files` | Normal ≤ 250 MB, preflight ≤ 1024 MB, approval ≤ 2048 MB, absolute 2048 MB |
| `models` | **Disabled**; future caps 3 GB normal, 5 GB with approval |
| `downloads` | **Disabled**; max 1024 MB, destination `browser/anu-downloads` |
| `packages` | System, apt, global pip and global npm installs all **disabled** |
| `execution` | Heavy workload: ≥ 1024 MB RAM or ≥ 60 % CPU |
| `behavior` | Low RAM → pause, low disk → deny, hot → pause, workspace full → deny, large file → approval, unknown resource → deny |

## `anu-command-gateway.yaml`

- Security: `shell: false`, `command_chaining: false`, `sudo: false`, `root_execution: false`
- Timeout 120 s, max output 128 KB
- Every command goes through the Governor; decisions `DENY`, `PAUSE`,
  `APPROVAL`, `PREFLIGHT` stop it
- Allowed commands: `pwd`, `git_version`, `git_status`, `git_status_short`,
  `git_branch`, `git_branch_current`, `git_diff`, `git_diff_stat`,
  `python_version`, `node_version`, `npm_version` (each with a fixed executable,
  fixed arguments, RAM and storage budget)
- `python_test` / `python_compile` are routed to the sandbox

## `anu-execution-profiles.yaml`

| Profile | RAM | Timeout | Filesystem | Sandbox |
|---|---|---|---|---|
| `inspect` | 128 MB | 30 s | workspace-read | no |
| `git-read` | 256 MB | 60 s | workspace-read | no |
| `python-basic` | 256 MB | 60 s | workspace-read | no |
| `system-info` | 128 MB | 30 s | read-only | no |
| `python-sandbox` | 512 MB | 120 s | workspace-read | **yes** |

All profiles: `network: false`.

## `anu-sandbox.yaml`

| Setting | Value |
|---|---|
| Image | `python@sha256:bb29887…` (pinned digest of `python:3.13-slim`) |
| Network | `none` |
| Privileged | false |
| Root filesystem | read-only |
| Capabilities | all dropped, `no_new_privileges` |
| Limits | 1 CPU, 512 MB RAM (no extra swap), 128 PIDs, 128 MB tmpfs |
| User | `999:984` |
| Workspace mount | read-only at `/workspace` |
| Operations | `python_test` (`python -m unittest discover -v /workspace`), `python_compile` (compile all `.py` in memory) |
| Image pull at run | **`never`** (`docker run --pull never` in `anu-sandboxd.py`) |

## `anu-executor.yaml`

Allowed: list, read, write, mkdir, delete file, delete empty directory, search.
Denied: shell, docker, git, network, deployment.
Limits: read 256 KB, write 512 KB, 200 list entries, 100 search results, 5000 files searched.

## `anu-projects.yaml`

Rules: explicit registration required, one repository per project, repository
must match exactly, production deployment requires approval.
V0.6.4: `projects` lists registered workspace keys (e.g. `sandbox-test-project`).
Unregistered directories are rejected by Project Intelligence / Safe Mutation /
template expand when `require_explicit_registration: true`.

## `anu-policy.yaml` (declarative)

Identity (`anu-agent`, home `/srv/anu/home`), writable / read-only / forbidden
paths, git / browser / docker / deployment all disabled, security switches all
off, resource limits, `max_attempts_per_task: 3`.

Not read by any code yet. It documents intent and must be kept consistent with
the enforcing files.

## Consistency Rules

These values must match wherever they appear (V0.5.9 alignment):

| Value | Files / code |
|---|---|
| Warning 60 °C, pause 65 °C | resources, admission, policy (pause) |
| Min free disk 50 GB | resources, admission |
| RAM 3072 MB, CPU 50 %, 1 parallel task | resources, admission, policy |
| 3 attempts per task | policy, `MAX_ATTEMPTS` in the Task Engine and task graph, `test_retry_exhaustion_v0_5_2.py` |
