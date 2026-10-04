# ANU Security Model

> **ANU must remain useful without becoming an unrestricted process on the
> host machine.**

This document defines what ANU must never be allowed to do, how its
permissions are layered, how secrets are protected, and the rules that apply
because the ANU source repository
([Sadhu2005/Anu-AI-Agent](https://github.com/Sadhu2005/Anu-AI-Agent)) is
public.

It describes the intended security architecture and the **current, verified**
controls (checked against the live Pi on 5 Oct 2026). Section 22 lists known
gaps. This document must be updated whenever a security boundary changes.

---

## 1. Security Principles

```text
Least privilege → Explicit capability → Controlled execution
               → Verification and recovery → Human control
```

- A component receives only the permissions it requires.
- A capability exists because there is an explicit architectural reason for it.
- No component receives broad host access merely because it is convenient.

---

## 2. What ANU Must Never Be Able to Do

These are prohibited for the ANU agent process unless the architecture is
explicitly redesigned, reviewed, and recorded in [DECISIONS.md](DECISIONS.md).

### 2.1 Unrestricted Host Shell Access

ANU must never turn a natural-language goal into
`/bin/sh -c "<arbitrary command>"` or any equivalent path.

**Current control:** the command gateway only accepts allow-listed command
IDs (`python_test`, `python_compile`), and every subprocess call uses
`shell=False` (ADR-007).

### 2.2 Direct Docker Administration

The `anu-agent` user must never be in the `docker` group. Docker on the host
does not mean the agent may control the Docker daemon.

```text
ANU agent → controlled sandbox request → anu-sandboxd (root) → Docker → restricted sandbox
```

The privileged Docker operation belongs to the sandbox service, not to ANU (ADR-008).

**Verified:** `id anu-agent` gives `groups=984(anu-agent)` only. The
Resource Governor also hard-denies the `docker` operation in code, whatever
the config says.

> The operator account (`anu-vps1947`) **is** in the `docker` group, which is
> root-equivalent. Protect its SSH key (`raspberrypi_anu`) accordingly.

### 2.3 Arbitrary Filesystem Modification

ANU must never write to `/`, `/etc`, `/usr`, `/boot`, `/bin`, `/sbin`,
`/lib`, `/root`, or other host-critical paths.

**Current control:** `ProtectSystem=strict` makes the whole filesystem
read-only to the service, except these explicit `ReadWritePaths`:

```text
/srv/anu/home        /srv/anu/browser
/srv/anu/brain       /srv/anu/logs
/srv/anu/workspace   /srv/anu/runtime
/srv/anu/sandbox     /srv/anu/secrets
/srv/anu/checkpoints /srv/anu/tests
```

(`checkpoints` is added by the drop-in `v0.5.9-checkpoints.conf`.) The
writable-path list must stay synchronized between `config/anu-policy.yaml`,
`deploy/systemd/`, and the live units ([CONFIGURATION.md](CONFIGURATION.md)).

### 2.4 System Package Installation

ANU must not autonomously install system packages. `config/anu-admission-policy.yaml`
declares:

```yaml
packages:
  system_install: false
  apt: false
  apt_get: false
  pip_global: false
  npm_global: false
```

**Current control:** the Resource Governor hard-denies the `package`
operation in code ("Host package installation is disabled by ANU policy"),
independent of the YAML. The service also has no network and no write
access to `/usr`. Dependency changes are deliberate administrative actions.

### 2.5 Unrestricted Network Access

```ini
PrivateNetwork=yes
RestrictAddressFamilies=AF_UNIX
```

The agent runtime has only a private loopback and Unix sockets; it cannot
reach the host network. The sandbox containers run with `network: none`.
Network-enabled capabilities, when introduced, must be explicit, narrowly
scoped, and separately governed. (Downloads are disabled in policy.)

### 2.6 Access to Host Secrets

ANU must never freely inspect `/root`, SSH private keys, cloud credentials,
API tokens, database passwords, system credentials, or unrelated application
secrets.

**Current control:** `ProtectHome=yes` hides `/home` and `/root` from the
service; it runs as an unprivileged user with an empty capability set.

A secret becomes available to a component only when a specific capability
requires it and the exposure is explicitly designed. See section 22 for the
`secrets/` directory gap.

### 2.7 Disabling Its Own Safety Controls

ANU must not be able to modify systemd security configuration, resource,
sandbox or credential policy, execution boundaries, or host security
configuration.

```text
ANU can operate inside its permissions.
ANU cannot grant itself new permissions.
```

**Current control:** `bin/`, `config/`, and `src/` are both `ReadOnlyPaths`
for the service **and** owned by root (two independent layers). Units live in
`/etc/systemd/system`, which `ProtectSystem=strict` makes read-only.
`NoNewPrivileges=yes` and `RestrictSUIDSGID=yes` block privilege escalation
through setuid binaries.

### 2.8 Unbounded Resource Consumption

| Layer | Limits |
|---|---|
| Application policy (Governor, Guardian) | RAM, CPU, parallel tasks (1), workspace (30 GB) and project (15 GB) storage, temperature, large files, model size, minimum free disk (50 GB) |
| Operating system (`anu-agent.slice`) | `CPUQuota=200%`, `MemoryHigh=2560M`, `MemoryMax=3072M`, `TasksMax=64`, `IOWeight=50` |
| Sandbox (per container) | 512 MB memory and swap, 1 CPU, 128 pids, 120 s timeout |

The OS layer is enforced by the kernel even if application code is wrong.
When application limits are violated the response is `pause`, `deny`, or
`approval`, never "continue anyway". The service also has
`OOMScoreAdjust=500`: under memory pressure, ANU dies before the host's
critical services.

---

## 3. Layered Permission Model

No single control is considered sufficient.

```text
                    USER
                      │
                 GOAL / TASK
                      │
              AGENT CONTROLLER
                      │
               POLICY / GOVERNOR
              ┌───────┴────────┐
          EXECUTION          RESOURCES
              │                │
        SAFE MUTATION       GUARDIAN
              │
        SANDBOX BOUNDARY
              │
        RESTRICTED EXECUTION
```

Below the application controls sits the operating-system boundary:

```text
Linux → systemd sandboxing + slice limits → anu-agent process (uid 999, no capabilities)
```

Security exists at six layers: application policy, execution controls, sandbox
controls, operating-system controls, filesystem permissions, and resource
governance.

---

## 4. Linux / systemd Boundary

`anu-agent.service` ([deploy/systemd/anu-agent.service](../deploy/systemd/anu-agent.service)):

```ini
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
LockPersonality=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
PrivateNetwork=yes
RestrictAddressFamilies=AF_UNIX
UMask=0077
```

These controls are part of the security boundary, not optional
optimizations.

**A new writable directory is a security decision.** It must never be added
simply because an operation produced a permission error. The V0.5.9
checkpoint fix is the model to follow: the path was added deliberately, in a
named drop-in, and recorded ([LESSONS-LEARNED.md](LESSONS-LEARNED.md)).

### The sandbox daemon is the most privileged component

`anu-sandboxd` runs as **root** because it talks to Docker. Its own
boundaries matter most:

- systemd: `PrivateNetwork`, `PrivateDevices`, `ProtectSystem=strict`,
  `ProtectHome`, only `/run/anu-sandbox` writable, `bin/` and `config/` read-only.
- Socket `/run/anu-sandbox/sandbox.sock` is `root:anu-agent`, mode `0660`.
- Every request's caller is checked with `SO_PEERCRED` against
  `AUTHORIZED_UID=999`.
- Only allow-listed commands are run. The project path must resolve inside
  the workspace, and it is mounted read-only.

Any change to `anu-sandboxd.py` or `anu-sandbox.yaml` requires security review.

---

## 5. Read-Only Code and Configuration

```text
code / configuration  (/srv/anu/bin, /srv/anu/config, /srv/anu/src)  → protected
runtime state         (ReadWritePaths)                                → controlled writable locations
```

ANU can generate and modify project work inside approved workspace
boundaries without being able to rewrite its own production control plane.

---

## 6. Safe Mutation Security

Code changes are never applied through host shell commands:

```text
baseline → checkpoint → mutation → test → verification → commit OR rollback
```

This separates *"ANU wants to change a file"* from *"the host filesystem has
been modified"* (ADR-003).

Path rules, enforced in `anu-safe-mutation-v0.2.py`:

- The project must resolve inside the workspace (`relative_to(WORKSPACE)`).
- Every target file and the changes file must resolve inside the project
  (`resolve_file` → `relative_to(project)`).
- A task-supplied path must never silently escape the project boundary.
  Since V0.5.9, relative paths resolve against the project, not the process
  working directory.

---

## 7. Sandbox Security

`config/anu-sandbox.yaml`:

```text
network            none
memory / swap      512 MB / 512 MB
cpus               1
pids limit         128
project mount      read-only (/workspace:ro)
root filesystem    read-only, writable tmpfs only
capabilities       all dropped
no-new-privileges  yes
user               999:984
timeout            120 s
image              python@sha256:bb2988715db2… (pinned by digest)
```

A moving tag such as `python:3.13` is not an immutable security reference;
the digest is. The sandbox must remain isolated, resource-limited,
network-disabled, filesystem-restricted, capability-restricted, and
time-bounded.

---

## 8. Secrets Handling

A public repository must never contain secret values.

### 8.1 Never Commit Secrets

Never commit passwords, API keys, access tokens, private keys, SSH keys,
database credentials, cloud credentials, session secrets, signing keys, or
certificates containing private key material. That includes secrets hidden
in `.env` and `.env.*` files, configuration files, test fixtures, logs,
database dumps, shell history, debug output, and "example" files with real
values.

`.gitignore` excludes `secrets/`, `brain/`, `logs/`, `.env*` (except
`.env.example`), key files (`*.pem`, `*.key`), and SQLite files.

### 8.2 Keep Secrets Outside the Repository

Secrets live in **`/srv/anu/secrets`**: owned by `anu-agent`, mode `700`,
never versioned. A secret should be supplied only to the process that
actually needs it.

### 8.3 Never Put Secrets in Logs

Logs must not contain Authorization headers, API tokens, passwords, private
keys, session cookies, or full secret values. Log metadata, not credentials:

```text
API request failed
credential: configured
status: authentication_failed
```

never:

```text
token=sk-................................
```

### 8.4 Never Put Secrets in the Brain

The brain is durable operational state, not a secret vault. Task
descriptions, observations, errors, and research records must never hold raw
credentials.

> **A database backup must be assumed to contain sensitive operational
> information.**

Brain backups ([OPERATIONS.md](OPERATIONS.md)) stay on the Pi or in private
storage, never in the repository.

---

## 9. Public Repository Rules

```text
Git repository = public, forever
```

| Safe to commit | Never commit |
|---|---|
| source code, tests, documentation | real credentials, private keys, tokens |
| example configuration with placeholders | the production database |
| schemas, non-sensitive policy | private user data |
| architecture diagrams, public sample data | secret configuration, credential-bearing logs |

Templates (`.env.example`, `config/example.yaml`) only with clearly fake
placeholder values.

---

## 10. Git Security Rules

Before every release commit, inspect `git status`, `git diff`, and
`git diff --cached`. **Tests passing is not a clean release; security review
is also required.**

Secret-pattern scan (a detection aid, not a substitute for review):

```bash
# staged changes
git diff --cached | grep -inE 'api_key|token *=|password *=|secret *=|PRIVATE KEY|Authorization:|Bearer [A-Za-z0-9]'
# whole history, before making anything public
git log --all -p | grep -inE 'api_key *=|token *=|password *=|PRIVATE KEY|Bearer [A-Za-z0-9]'
```

**Last full-history scan:** 5 Oct 2026. No matches.

---

## 11. Credential Rotation

An exposed credential is compromised. Deleting the line is not enough.

```text
discover exposure → revoke credential → issue replacement
→ remove material from repository/history as appropriate → audit affected systems
```

Removing a secret from the current file does not make an already-public
commit safe; GitHub forks and caches may hold it.

---

## 12. Human Approval Boundaries

Some operations stay approval-gated: large files (policy: `when_large_file:
approval`), large model resources, sensitive credentials, irreversible
external actions, security-sensitive configuration, and capability expansion.

The goal is not to eliminate human control but to apply it where judgment is
genuinely required. The full matrix (AUTONOMOUS / APPROVAL / DENY) is planned
for [V0.6.7](versions/v0.6.7.md).

---

## 13. Security of Autonomous Execution

```text
observe → plan → execute within policy → verify → record → continue / wait / retry / stop
```

Resource and policy conditions are authoritative on **every** cycle: the
scheduler runs only when the Guardian reports `NORMAL`.

**Importance does not override policy.** "The task is important" is never
permission to bypass resource limits, filesystem restrictions, sandbox rules,
approval requirements, or security controls.

---

## 14. Failure Must Fail Closed

When ANU cannot determine whether an action is safe, it stops or denies.

> **Unknown capability must not automatically become allowed capability.**

**Current control:**

- The Governor accepts a fixed set of operations; anything else is rejected.
- The gateway rejects unknown command IDs.
- The sandbox rejects unknown callers and commands.
- The scheduler does not run in `WARNING` or `PAUSE`.

The policy's `behavior:` block (`when_unknown_resource: deny`, `when_hot:
pause`, …) documents this intent but is not read by code. The denials are
implemented directly.

---

## 15. Security and the Agent Model

Capability is not trust. A model may produce incorrect reasoning, unsafe
suggestions, malicious-looking strings, invalid commands, unexpected paths,
or wrong assumptions. **Model output is data until validated by the control
layer.**

```text
model / planner → structured intent → validation → policy
               → controlled executor → sandbox / approved capability
```

never `model → host shell`. Models are currently disabled (ADR-004); see
[VISION.md](VISION.md) section 12.

---

## 16. Security Invariants

| # | Invariant | Enforced today by |
|---|---|---|
| 1 | ANU cannot grant itself new OS privileges | `NoNewPrivileges`, empty capability set, `RestrictSUIDSGID` |
| 2 | ANU cannot directly administer Docker | not in `docker` group; Governor denies `docker`; only `anu-sandboxd` talks to Docker |
| 3 | ANU cannot modify protected host paths | `ProtectSystem=strict`, `ReadOnlyPaths`, root ownership of `bin/` and `config/` |
| 4 | ANU cannot freely access unrelated secrets | `ProtectHome`, unprivileged uid (see gap G1) |
| 5 | Autonomous tasks remain subject to resource governance | Guardian gate every cycle, Governor admission, slice limits |
| 6 | Unknown or disallowed operations fail closed | allow-lists in Governor, gateway, sandbox |
| 7 | Controlled code mutation has a verification and recovery path | Safe Mutation checkpoint → test → rollback (see gap G2) |
| 8 | The public repository contains no production secrets | `.gitignore`, pre-release scan, review |

Any feature that would violate an invariant requires an explicit security
review and an ADR.

---

## 17. Security Review Before a Version Freeze

Part of the freeze audit ([WORKFLOW.md](WORKFLOW.md) section 14):

```text
[ ] working tree reviewed
[ ] diff reviewed
[ ] new permissions identified
[ ] new writable paths identified
[ ] new network access identified
[ ] new secret access identified
[ ] sandbox / anu-sandboxd changes reviewed
[ ] systemd unit changes reviewed (diff deploy/systemd/)
[ ] secret-pattern scan clean
[ ] security-sensitive tests passed
```

A feature is not security-neutral because it is small. A one-line
`ReadWritePaths` change can matter more than hundreds of lines of
application code.

---

## 18. Public Disclosure

Security through obscurity is not the design principle. Anyone reviewing the
repository should understand what ANU can and cannot access, where code
executes, where data is written, how sandboxing works, where secrets belong,
and what requires approval. This document does that without publishing
credentials or sensitive operational data.

---

## 19. Security Incident Response

```text
STOP AUTONOMOUS EXECUTION      sudo systemctl stop anu-agent.service
PRESERVE EVIDENCE              copy logs/, journalctl -u anu-agent, brain backup
IDENTIFY THE BOUNDARY FAILURE
REVOKE EXPOSED CREDENTIALS
CONTAIN THE SYSTEM             also stop anu-sandbox.service if the sandbox is involved
FIX THE CONTROL
ADD A REGRESSION TEST
REVIEW THE ARCHITECTURE        record an ADR if the boundary changes
ONLY THEN RESUME AUTONOMY
```

Record the incident in [LESSONS-LEARNED.md](LESSONS-LEARNED.md) and
[HISTORY.md](HISTORY.md) so the same class of failure is not rediscovered.

---

## 20. Security From V0.7 to V1.0

```text
V0.7  controlled autonomous execution
V0.8  more capable planning and project interaction
V0.9  longer-lived autonomous goals
V1.0  trusted personal autonomous worker
```

Capability must never advance by simply removing restrictions:

```text
more capability + more explicit policy + better isolation + better auditability = safer autonomy
```

---

## 21. Final Security Rule

> **ANU may be autonomous inside its boundaries, but it must never be
> autonomous in defining those boundaries.**

The agent decides how to pursue an approved objective. The security
architecture decides what the agent is allowed to do. That separation must
remain intact through V1.0 and beyond.

---

## 22. Known Gaps

Found while verifying this document against the live system. Each should be
closed by a planned version or explicitly accepted in an ADR. G1–G3 are
tracked as explicit items, each with a required negative test, in the
[V0.6.9 hardening checklist](versions/v0.6.9.md). A gap is closed only
when there is evidence that the prohibited behaviour is prevented.

| ID | Gap | Risk | Proposed fix |
|---|---|---|---|
| G1 | `/srv/anu/secrets` is a `ReadWritePath` for the whole agent process | Any agent code path can read **and modify** every secret, contrary to "only the component that needs it" | Make it read-only for the service (`ReadOnlyPaths`). Later, deliver secrets per capability (e.g. systemd `LoadCredential=`) |
| G2 | `/srv/anu/tests` is a `ReadWritePath`, owned by `anu-agent` | ANU could alter its own regression tests, weakening the verification it is judged by | Make tests root-owned and read-only to the service (follows from the root-owned checkout in WORKFLOW.md) |
| G3 | `/srv/anu/.git` is owned by `anu-agent` | Not writable by the service (`ProtectSystem=strict`), but the `anu-agent` user owns the repository history and hooks | Make it root-owned ([WORKFLOW.md](WORKFLOW.md) section 25) |
| G4 | `logs/` is writable by the agent | The agent could rewrite its own application log | The systemd journal (`journalctl -u anu-agent`) is the tamper-resistant record. Treat it as authoritative in audits |
| G5 | Admission `behavior:` block and `anu-policy.yaml` are not read by code | Editing them changes nothing; readers may assume they are enforced | Policy consistency gate ([V0.6.6](versions/v0.6.6.md)) |
| G6 | Operator account is in the `docker` group | Compromise of the operator SSH key gives root | Accepted for administration; keep the key passphrase-protected and the Pi off the public internet |
