---
name: anu-testing-gate
description: >-
  Enforces ANU unit, integration, functional, and regression testing for every
  code change, then verifies on the Raspberry Pi by push → SSH → pull/checkout
  → run tests → fix failures. Use when implementing features, fixing bugs,
  editing bin/config/tests, preparing a version freeze, deploying to the Pi,
  running the test matrix, or when the user mentions testing, regression,
  functional tests, integration tests, unit tests, Pi verify, or server check.
---

# ANU Testing Gate

Apply this skill for **every meaningful code change**, including after each
code file write. A change is not done until local gates pass **and** the Pi
verifies the same commit (or a safe `/tmp` pack). Fix failures before moving
on. Before a commit or push, and when choosing a version branch, also follow
[anu-version-structure](../anu-version-structure/SKILL.md).

Canonical docs: [docs/TESTING.md](../../../docs/TESTING.md),
[docs/WORKFLOW.md](../../../docs/WORKFLOW.md),
[docs/PI-OPS.md](../../../docs/PI-OPS.md).

## Checklist (copy and track)

```text
ANU Testing Gate:
- [ ] Map change → affected layers + test files
- [ ] Add/update versioned test if behaviour changed
- [ ] Local: AST / py_compile + portable regression packs
- [ ] Push candidate commit to origin
- [ ] Pi: fetch + checkout that exact commit (or /tmp pack)
- [ ] Pi: unit → integration → functional → regression (stop on fail)
- [ ] Fix → re-push → re-verify until green
- [ ] Restart services only after green (if production path touched)
```

## Test layers (required order)

| Layer | What it proves | Where to look in this repo |
|---|---|---|
| **Unit** | One module / pure logic | Small `tests/test_*` focused on one helper; `bin/anu-test-matrix` unit discovery |
| **Integration** | Boundaries between components | Scheduler↔agent, agent↔task engine, policy↔resources, git/GitHub gates |
| **Functional** | End behaviour of a feature path | Goal cycle, safe mutation, approvals, dashboard APIs, lab assign |
| **Regression** | Prior versions still pass | `tests/run_v*_regression.py`, full `test_*.py` loop on Pi |
| **E2E / production** | Real systemd → runtime path | Live Pi smoke + sandbox-test-project (service often stopped) |

Never claim pass without exit code `0`. Missing optional layers are **SKIPPED**, not PASS.

## Where to check (project map)

| Changed path | Minimum checks |
|---|---|
| `bin/*.py` | Matching `tests/test_<feature>_vX_Y_Z.py` + version regression pack |
| `config/*.yaml` | YAML load + config-drift (`tools/check_doc_config_drift.py`) + packs that load that config |
| `tests/*` | Run the new/changed test alone, then its `run_v*_regression.py` |
| `deploy/systemd/*` | Pi unit install + `systemctl` status + journal smoke |
| `docs/versions/*` | Tests listed in version doc match files that exist and pass |
| Lab / autonomy | `tests/run_v0_9_0_regression.py` and/or `tests/run_v0_9_lab_regression.py` |

Test conventions (from TESTING.md):

- Name: `tests/test_<feature>_v<major>_<minor>_<patch>.py`
- Standalone script: exit `0` / non-zero; print clear `PASS` / `FAIL`
- Prefer temp DB / temp dir; avoid live `brain/anu-brain.db` unless intentional
- Use `python3 -B` (no `__pycache__`)

## Local gate (workspace, Windows)

Run before push:

```powershell
python -m py_compile (Get-ChildItem bin\*.py, tests\*.py, tools\*.py | ForEach-Object FullName)
python tests/run_v0_9_0_regression.py
# if lab/autonomy touched:
python tests/run_v0_9_lab_regression.py
```

Also run the **closest** version pack for the change (e.g. `tests/run_v0_8_13_regression.py`).
CI mirror: `.github/workflows/ci.yml` (`py_compile` + portable packs).

Optional matrix detect:

```powershell
python bin/anu-test-matrix-v0.1.py
```

## Server verify (Pi) — required for Pi-path or freeze work

Path of truth:

```text
workspace → commit → git push origin → SSH Pi → git fetch → checkout same commit → test → fix loop
```

**Do not** overwrite production with blind `scp` of random files for release
verification. Prefer git. For a fast no-sudo pack (throwaway `/tmp`), use
`tools/pi-run-pack.ps1` (see [reference.md](reference.md)).

### 1. Push from workspace

```powershell
git status --short
git push -u origin HEAD
git rev-parse HEAD
```

Record the SHA. The Pi must verify **that** SHA.

### 2. Connect and pull/checkout

```powershell
ssh anu-vps1947@raspberrypi
```

On the Pi:

```bash
PIGIT="sudo git -c safe.directory=/srv/anu -C /srv/anu"
$PIGIT fetch origin --tags
$PIGIT status --short   # clean before switch; stash/backup unexpected dirt
# candidate branch example — use the branch/SHA you pushed:
$PIGIT checkout --detach origin/<branch-or-sha>
$PIGIT log -1 --oneline
```

PowerShell remote tips ([PI-OPS.md](../../../docs/PI-OPS.md)): no `&&`; avoid nested `\"`; prefer temp `.sh` + `scp` + `bash`.

### 3. Run tests on the Pi

If tests touch the live brain or scheduler lock:

```bash
sudo systemctl stop anu-agent.service
```

Prefer throwaway base when possible (`ANU_BASE=/tmp/anu-pack-…`).

Single test:

```bash
sudo -u anu-agent python3 -B /srv/anu/tests/test_<feature>_vX_Y_Z.py
```

Version regression pack (example):

```bash
sudo -u anu-agent python3 -B /srv/anu/tests/run_v0_9_0_regression.py
```

Full stop-on-fail loop:

```bash
sudo -u anu-agent bash -c '
  cd /srv/anu/tests
  for t in test_*.py; do
    python3 -B "$t" > /dev/null 2>&1 && echo "PASS $t" || { echo "FAIL $t"; exit 1; }
  done
'
```

From Windows without sudo (upload pack):

```powershell
powershell -File tools/pi-run-pack.ps1 -Pack tests/run_v0_9_0_regression.py
```

### 4. Fix loop (mandatory)

On any failure:

1. Capture failing file + stderr tail (redact secrets).
2. Fix in the **workspace** (not ad-hoc edits as the only copy on the Pi).
3. Add/adjust a regression test that would have caught it.
4. Re-run local gate → push → Pi fetch/checkout → re-run failing test then pack.
5. Repeat until green.

Do not mark the task complete while Pi verification is red.

### 5. After green (production path)

```bash
# if units changed: copy from deploy/systemd + daemon-reload
sudo systemctl restart anu-agent.service
# if dashboard touched:
sudo systemctl restart anu-dashboard.service
sudo systemctl --no-pager -l status anu-agent.service
sudo journalctl -u anu-agent.service --no-pager -n 80
```

Expect healthy idle: `Health check: status=NORMAL`, scheduler cycle lines — not crashes.

## When to skip Pi

Skip **only** if the user explicitly says local-only, or the change is docs-only
with no runtime impact. Still run local AST/link sanity. Never skip Pi for
freeze, deploy, autonomy, safe mutation, or live-path work.

## Report format

End with a short gate report:

```text
Gate: PASS|FAIL
SHA: <commit>
Local: py_compile / packs …
Pi: host=anu-vps1947@raspberrypi checkout=<ref> packs…
Failures: <none | file + reason>
Fixes applied: <none | summary>
```

## More detail

- Commands, pack helpers, live-brain warnings: [reference.md](reference.md)
