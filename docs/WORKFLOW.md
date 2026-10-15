# ANU: Version Build and Deployment Workflow

This document defines how an ANU version is developed, tested, frozen,
documented, tagged, and deployed to the Raspberry Pi. The goal is to make every
release **reproducible and auditable**.

```text
Plan → Branch → Implement → Test → Integration Test → Freeze Audit
     → Commit → Tag → Documentation → Deploy to Pi → Production Verification
```

---

## 1. Core Principle

A version is not complete because the code "looks finished". It becomes a
release only when **code, tests, integration, runtime behaviour,
documentation, git history, and deployment verification** all agree.

> **A version is a verified state of the system, not just a set of code changes.**

---

## 2. The Three Environments

```text
Development workspace   D:\Anu-AI-Agent (Cursor, Windows)    write code and docs
        ↓ git push
Git repository          github.com/Sadhu2005/Anu-AI-Agent     versioned source of truth
        ↓ git fetch
Raspberry Pi runtime    /srv/anu (anu-agent)                  tests and production
```

- The repository is the source of truth. The Pi is never the only copy of the code.
- Docs are written in the workspace, never edited on the Pi.
- The workspace keeps LF line endings (`core.autocrlf=false`), because the code
  runs on Linux.

### Interim constraint: tests run only on the Pi

Code and tests hard-code `/srv/anu`, so the regression suite cannot run in the
workspace. Until paths are configurable, **testing happens on the Pi from a
pushed candidate commit**, and the tag is placed on that exact commit after it
passes (section 15). Nothing is ever edited in place on the Pi.

---

## 3. Branching

```text
v<major>.<minor>.<patch>-integration      active development of one version
v<major>.<minor>.<patch>                  tag = frozen release (immutable)
```

```text
branch = development line
tag    = exact frozen release
```

A new version starts from the previous **frozen** state:

```bash
git fetch origin --tags
git checkout -b v0.6.0-integration v0.5.9      # from the previous tag
git push -u origin v0.6.0-integration
```

Development for the next release stays isolated from the previous frozen release.

---

## 4. Start From a Known Baseline

```bash
git status --short          # must be empty
git log -1 --oneline        # e.g. 1996199 V0.5.8 add agent-aware scheduler lifecycle boundary
git branch --show-current
```

Never start a version from an unknown working tree.

---

## 5. Define the Scope First

Create `docs/versions/vX.Y.Z.md` from [versions/_TEMPLATE.md](versions/_TEMPLATE.md)
**before coding**: what changes, why, which tests prove it, and what
production behaviour should result (exit criteria).

Do not mix unrelated work into a release.

---

## 6. Implement in Small Steps

```text
change → test → change → test → … → production execution
```

Each meaningful change gets a regression test where practical
(`tests/test_<feature>_vX_Y_Z.py`). This is how real ANU bugs (`goal_id` type,
changes-file path, systemd checkpoint access, config drift) became durable
improvements. See [LESSONS-LEARNED.md](LESSONS-LEARNED.md).

---

## 7. Syntax Gate

Before deeper testing, every changed Python file must parse. In the workspace:

```bash
git diff --name-only --diff-filter=AM -- '*.py' | xargs -r python -B -c \
  "import ast,sys; [ast.parse(open(f,encoding='utf-8').read(),f) or print('AST_OK',f) for f in sys.argv[1:]]; print('AST_GATE_PASS')"
```

On the Pi:

```bash
sudo -u anu-agent python3 -B - <<'PY'
import ast, pathlib
for p in sorted(pathlib.Path("/srv/anu/bin").glob("*.py")):
    ast.parse(p.read_text(encoding="utf-8"), filename=str(p)); print("AST_OK", p.name)
print("AST_GATE_PASS")
PY
```

A syntax failure stops the workflow immediately.

---

## 8. Component Tests

Run the tests that cover the changed component, then the **whole** regression
suite. A change is never judged by its new test alone. See
[TESTING.md](TESTING.md) for commands and the suite inventory.

---

## 9. Integration Tests

Test the boundaries, where most real ANU bugs happened:

```text
Task → AgentGoal
Scheduler → AgentController
AgentController → Task Engine
Task Engine → Safe Mutation
Safe Mutation → Sandbox
Sandbox → resource admission
```

---

## 10. Production-Boundary Tests

Isolated tests passing is not production readiness. Test the real path:

```text
systemd → anu-runtime.py → Resource Guardian → agent-aware scheduler
→ AgentController → Task Engine → Safe Mutation → sandbox
```

The closer a test is to this path, the more valuable it is for release.

---

## 11. Resource Gate

```bash
free -h
df -h /srv/anu
vcgencmd measure_temp
sudo -u anu-agent python3 -B /srv/anu/bin/anu-resource-guardian.py | grep '"status"'
```

Autonomous verification runs only when the Guardian reports `NORMAL`.

---

## 12. Clean Working Tree Gate

```bash
git status --short
git diff --check
```

On the Pi, git always runs **as root** on a root-owned repository (see
section 25, step 1):

```bash
PIGIT="sudo git -c safe.directory=/srv/anu -C /srv/anu"
$PIGIT status --short
```

Why root: `bin/` and `config/` are root-owned so that ANU can never modify its
own code. A checkout must therefore be done by root, and root must never run
git inside a repository whose hooks another user can write.

No unexplained files or modifications. Never add unrelated files to a release commit.

---

## 13. Exact File Review

```bash
git diff --name-status
git diff --stat
git diff -- <exact-files>
git ls-files --others --exclude-standard     # new files, e.g. tests
```

The release has an explicit file list.

---

## 14. Freeze Audit

Establish and record: branch, HEAD, working-tree state, changed files, syntax,
tests, integration, configuration consistency, runtime boundary, service
state, documentation.

**Stop** if the audit shows: unintended changes, syntax failures, missing
tests, configuration contradictions, runtime failures, or unknown production
behaviour.

---

## 15. Commit, Test on the Pi, Then Tag

### 15.1 Commit the candidate (workspace)

```bash
git add <explicit files>
git diff --cached --check
git diff --cached --stat
git commit -m "V0.5.9 <architectural milestone>"
git push origin v0.5.9-integration
```

Commit messages describe the architectural milestone, for example
`V0.5.8 add agent-aware scheduler lifecycle boundary`,
`V0.5.6 preserve agent execution results`.

### 15.2 Verify the candidate (Pi)

```bash
PIGIT="sudo git -c safe.directory=/srv/anu -C /srv/anu"
$PIGIT fetch origin --tags
$PIGIT status --short                                 # must be clean
$PIGIT checkout --detach origin/v0.5.9-integration
$PIGIT log -1 --oneline                               # the candidate commit
```

Then apply section 19 (units, migration), restart, and run sections 7–11 and
20–21 on the Pi.

### 15.3 Tag the verified commit (workspace)

Only if the Pi verification passed, tag **the same commit**:

```bash
git tag -a v0.5.9 <candidate-commit> -m "ANU V0.5.9"
git push origin v0.5.9
git show v0.5.9 --no-patch
```

If verification failed, fix, commit a new candidate, and repeat 15.2. Never
tag an unverified commit.

---

## 16. Documentation Update

New behaviour means the documentation reflects it, in the same release.

| Always | When relevant |
|---|---|
| `docs/versions/vX.Y.Z.md`: status, changes, tests, **evidence** | `docs/ARCHITECTURE.md`: new components or flow |
| `docs/CHANGELOG.md`: one entry | `docs/DECISIONS.md`: a new or superseded decision |
| `docs/README.md`: version index status | `docs/LESSONS-LEARNED.md`: a real bug and its rule |
| `docs/ROADMAP.md`: maturity | `docs/SETUP.md`, `docs/OPERATIONS.md`, `docs/CONFIGURATION.md`, `docs/DATABASE.md`, `docs/TESTING.md`: when their subject changed |
| | `docs/HISTORY.md`: milestones in the git-era table |

Rules for the content:

- **History** keeps the evolution; pre-git backups are artefacts until verified.
- **Decisions** answer "Why did ANU do this?" without reverse-engineering git.
- **Lessons:** important bug → root cause → fix → regression → lesson.

---

## 17. Getting Code to the Pi

```text
workspace → commit → GitHub → Pi fetches → checks out exact commit or tag → verification
```

The Pi deploys a **known git commit or tag**, never copied files.

### Do not deploy by blind file copy

```text
copy random Python files → overwrite production → restart → hope
```

makes it impossible to answer: Which version is running? Which commit? Which
files changed? Can we reproduce it? Can we roll back? Git answers all of these.

---

## 18. Production Deployment

```bash
PIGIT="sudo git -c safe.directory=/srv/anu -C /srv/anu"
$PIGIT fetch origin --tags
$PIGIT status --short             # clean before switching
$PIGIT checkout v0.5.9
$PIGIT describe --tags --always   # the Pi states what it runs
```

---

## 19. Configuration, Units, and Database

Code and runtime configuration are verified separately:

```text
source code version · configuration version · systemd units
sandbox definition · database schema version
```

**Configuration** lives in `config/` and ships with the code. Run the alignment
checks in [SETUP.md](SETUP.md) section 8. (A policy consistency gate that
rejects inconsistent deployments is planned for V0.6.6.)

**systemd** units live in `deploy/systemd/`. If they changed:

```bash
sudo cp /srv/anu/deploy/systemd/<unit> /etc/systemd/system/
sudo systemctl daemon-reload
```

**Database:** the brain is operational state. Never replace it during a code
deployment. For schema changes: **backup → migrate → verify → runtime**
([OPERATIONS.md](OPERATIONS.md)).

Restart only when the release requires it:

```bash
sudo systemctl restart anu-sandbox.service     # if sandbox code/config changed
sudo systemctl restart anu-agent.service
sudo systemctl show anu-agent.service -p MainPID -p ActiveState -p SubState -p ExecMainStartTimestamp
```

---

## 20. Production Smoke Test

```bash
sudo journalctl -u anu-agent.service --no-pager -n 100
sudo tail -100 /srv/anu/logs/anu.log
```

A healthy idle system shows `Health check: status=NORMAL` and
`Scheduler cycle completed: results=0` every minute, not crashes.

---

## 21. Autonomous Task Verification

For versions that change autonomous execution, use the dedicated
`sandbox-test-project` with a small, reversible task:

```text
Task → Scheduler → AgentController → Task Engine → Safe Mutation → Checkpoint
→ Mutation → Sandbox Test → COMMITTED → transaction in brain → Task COMPLETE
```

and a deliberate failure:

```text
Mutation → Test failure → Rollback → Integrity verified → ROLLED_BACK
```

Such a release is not production-ready until both behaviours are demonstrated.

---

## 22. Rollback

Every deployment has a known rollback target (the previous tag):

```bash
sudo systemctl stop anu-agent.service
sudo git -c safe.directory=/srv/anu -C /srv/anu checkout v0.5.8
# restore units from that tag's deploy/systemd/ if they differ; daemon-reload
# restore the brain backup taken before deployment, if the schema changed
sudo systemctl start anu-agent.service
```

A code checkout alone is not always a complete rollback. Consider **code,
configuration, database schema, systemd units, sandbox, and runtime state**.

---

## 23. Release Record

Every frozen release records, in the **Evidence** section of its
`docs/versions/vX.Y.Z.md`:

```text
Version · Git tag · Git commit · Branch · Purpose · Changed files
Tests run and results · Freeze audit result · Deployment result
Known limitations · Documentation updated · Rollback target
```

---

## 24. Definition of "Frozen"

```text
[ ] scope complete
[ ] syntax passes
[ ] relevant tests pass
[ ] full regression suite passes
[ ] integration tests pass
[ ] production boundary verified
[ ] configuration reviewed and consistent
[ ] working tree clean (workspace and Pi)
[ ] release commit created and pushed
[ ] Pi verified on that exact commit
[ ] release tag created on that commit and pushed
[ ] documentation updated (including evidence)
[ ] Pi deployment verified
```

Then **FROZEN** means:

> **The tagged git state is the authoritative implementation of that version.**

---

## 25. Adopting This Workflow (One-time)

**Steps 1–4 done on 5 Oct 2026 04:31 UTC.** `/srv/anu/.git` is root-owned,
`origin` is GitHub, and the Pi runs detached at tag `v0.5.9` (`73967ee`) with
a clean tracked working tree. Before checkout, every file the tag would write
was checked to equal either the tag or the last Pi commit. There were no
conflicts, so nothing was stashed. Backup of the previous `.git`:
`/var/backups/anu-workflow-20261005T043055Z/`. Steps 5–7 remain.

The Pi had, until then, been developed in place. To switch over:

1. **Make the Pi repository root-owned.** Today `/srv/anu/.git` belongs to
   `anu-agent` while every file in `bin/` and `config/` belongs to root. That
   means `anu-agent` cannot check out code, and root must not run git in a
   repository whose hooks `anu-agent` can write.

   ```bash
   sudo systemctl stop anu-agent.service
   sudo ls /srv/anu/.git/hooks | grep -v '\.sample$' || echo "no active hooks"   # inspect first
   sudo chown -R root:root /srv/anu/.git /srv/anu/.gitignore
   ```

2. **Connect the Pi to GitHub.** Its repository has no remote yet:

   ```bash
   PIGIT="sudo git -c safe.directory=/srv/anu -C /srv/anu"
   $PIGIT remote add origin https://github.com/Sadhu2005/Anu-AI-Agent.git
   $PIGIT fetch origin --tags
   ```

   (The *service* has no network, `PrivateNetwork=yes`; root running git
   from a shell does.)

3. **Commit the V0.5.9 work from the workspace.** The workspace already
   contains the Pi's uncommitted V0.5.9 changes (verified byte-identical),
   plus the aligned config, docs, and `deploy/systemd/`. Commit, push.

4. **Move the Pi onto the pushed commit.** Confirm its local changes match the
   commit, then:

   ```bash
   $PIGIT diff origin/v0.5.9-integration --stat      # expect only config/docs/deploy/README differences
   $PIGIT stash push -u -m "pre-workflow V0.5.9 in-place edits"
   $PIGIT checkout --detach origin/v0.5.9-integration
   sudo systemctl start anu-agent.service
   ```

   Note that this also deploys the aligned config: new work then stops at
   60 °C instead of 65 °C.

5. **Update the workspace `pi` remote.** It reads the Pi repository as
   `anu-agent` (`remote.pi.uploadpack = sudo -u anu-agent git-upload-pack`).
   Once GitHub is the bridge it is no longer needed:
   `git remote remove pi`.

6. **Tag past releases retroactively** where tags are missing:
   `v0.5.6` → `9fe804f`, `v0.5.7` → `718441c`, `v0.5.8` → `1996199`
   (`v0.5.4` has no single freeze commit; its last checkpoint is `d896412`).

7. **Retire in-place backups.** `bin/*.backup-*` and `*.pre-*` files are no
   longer needed once every change is a commit. Archive them outside
   `/srv/anu/bin`.

---

## 26. History So Far

The project already followed much of this process:

```text
V0.5.3   dependency admission → freeze (tag)
V0.5.4   ten bounded checkpoint commits → production dispatch
V0.5.5   agent controller lifecycle → freeze (tag)
V0.5.6   adapters → execution result preservation → regression
V0.5.7   WAITING lifecycle → re-wake → freeze
V0.5.8   agent-aware scheduler → real agent wake chain → commit 1996199 → clean tree
V0.5.9   production runtime → resource-gated scheduler → autonomous task → boundary debugging
```

What changes now: code is written in the workspace, tests run on a pushed
candidate, and the Pi only ever runs an identifiable commit.

---

## 27. Release Philosophy

ANU is a safety-sensitive autonomous system, so:

```text
fast development  ≠  unsafe deployment
```

The Pi follows the same philosophy as the release process:

```text
ANU is patient. ANU can wait. ANU can test. ANU can recover.
```

> **Build carefully, verify the boundaries, freeze a known state, and deploy
> only what we can identify and reproduce.**
