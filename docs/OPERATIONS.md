# ANU Operations Runbook

How to run, observe, and recover ANU on the Raspberry Pi. All commands run on
the Pi (`ssh anu-pi`). Commands that touch `/srv/anu` state need `sudo`.

## Processes

| Process | Runs as | Started by |
|---|---|---|
| `bin/anu-runtime.py` | `anu-agent` | `anu-agent.service`, inside `anu-agent.slice` |
| `bin/anu-sandboxd.py` | `root` | `anu-sandbox.service` |

Unit files are versioned in `deploy/systemd/`; see [SETUP.md](SETUP.md).

## Service Control

```bash
sudo systemctl status anu-agent
sudo systemctl restart anu-agent      # reloads code and config
sudo systemctl stop anu-agent         # stop autonomous work
sudo systemctl start anu-agent
journalctl -u anu-agent -n 100 --no-pager

sudo systemctl status anu-sandbox     # sandbox daemon (root)
ls -l /run/anu-sandbox/sandbox.sock   # srw-rw---- root anu-agent
systemctl show anu-agent.slice -p MemoryCurrent -p MemoryMax -p CPUQuotaPerSecUSec
```

Code and config are loaded at start, so **restart after deploying changes**.

## Is ANU Healthy?

```bash
sudo cat /srv/anu/runtime/anu-state.json       # last health check
sudo tail -n 30 /srv/anu/logs/anu.log          # runtime log
ps -eo user,pid,etime,cmd | grep /srv/anu/bin  # both processes running
```

Healthy log pattern, once a minute:

```text
[ANU] Health check: status=NORMAL temp=46.3C memory_available=7368MB
[ANU] Scheduler cycle completed: results=0
```

| Log line | Meaning |
|---|---|
| `Scheduler skipped: resource status=WARNING` | ≥ 60 °C, no new work started |
| `Scheduler skipped: resource status=PAUSE` | ≥ 65 °C, low RAM, or low disk |
| `Scheduler cycle completed: results=N` | N tasks dispatched (max 1) |
| `Scheduler cycle failed: <Error>` | Exception caught; runtime keeps running. Investigate. |

## Logs

| File | Content |
|---|---|
| `logs/anu.log` | Runtime health and scheduler cycles |
| `logs/anu-task-engine.jsonl` | Task lifecycle events |
| `logs/anu-mutations.jsonl` | Safe Mutation transactions |
| `logs/anu-actions.jsonl` | Command gateway actions |
| `logs/anu-events.jsonl`, `anu-errors.jsonl`, `anu-tests.jsonl` | Currently unused (empty) |

No log rotation yet (planned in V0.6.8).

## Tasks

The Task Engine CLI (`bin/anu-task-engine-v0.5.3.py`):

```bash
TE="sudo -u anu-agent python3 /srv/anu/bin/anu-task-engine-v0.5.3.py"

$TE list --limit 20
$TE show   --task-id 186
$TE events --task-id 186 --limit 50
$TE create --title "..." --description "..." --priority 5 [--goal-id N] [--parent-task-id N]
$TE run    --task-id N --project sandbox-test-project --changes-file .changes.json
$TE retry  --task-id N --project sandbox-test-project --changes-file .new-changes.json
```

Task dependencies (`bin/anu-task-graph-v0.1.py`):

```bash
TG="sudo -u anu-agent python3 /srv/anu/bin/anu-task-graph-v0.1.py"
$TG add   --task-id 12 --depends-on 11
$TG list  --task-id 12
$TG ready --task-id 12
```

### Making a task autonomous

For the **scheduler** to execute a task by itself, the task needs:

1. status `pending` or `ready`, with all dependencies satisfied
2. an execution specification (`task_execution_specs`: project,
   changes file, operation), saved through
   `save_execution_descriptor()` in `bin/anu-task-execution-spec-v0.1.py`
   (no CLI yet)
3. a changes file inside the project (`workspace/projects/<project>/`)

### Changes file format

```json
[
  {"file": "app.py", "old": "return 1", "new": "return 2"}
]
```

Each `old` must occur **exactly once** in its file. Paths are relative to the
project.

### Safe Mutation outcomes

| Status | Meaning |
|---|---|
| `COMMITTED` | Change applied, test passed |
| `ROLLED_BACK` | Test failed, files restored, hashes and test verified |
| `ABORTED_BASELINE_FAILURE` | Project tests failed *before* the change; nothing touched |
| `ROLLBACK_VERIFICATION_FAILED` | Restore could not be verified. **Inspect manually.** |
| `MUTATION_ENGINE_ERROR` | Exception (bad path, missing file, `old` not found exactly once) |

### Re-running a blocked task

`blocked` is terminal by design; there is no CLI to unblock. After fixing the
cause, with the service stopped and the brain backed up:

```bash
sudo -u anu-agent python3 - <<'EOF'
import sqlite3
db = sqlite3.connect("/srv/anu/brain/anu-brain.db")
db.execute("UPDATE tasks SET status='ready', updated_at=datetime('now') WHERE id=? AND status='blocked'", (186,))
db.commit()
print(db.execute("SELECT id, status, attempts FROM tasks WHERE id=186").fetchone())
EOF
```

Decide deliberately whether to also reset `attempts`. Creating a fresh
equivalent task is the cleaner alternative.

## Backups

### Brain (consistent online copy)

```bash
sudo -u anu-agent python3 - <<'EOF'
import sqlite3, datetime
src = sqlite3.connect("/srv/anu/brain/anu-brain.db")
name = "/srv/anu/brain/anu-brain.db.backup-" + datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
dst = sqlite3.connect(name)
src.backup(dst); dst.close(); print(name)
EOF
```

Do not copy `anu-brain.db` with `cp` while the service runs (WAL mode).

### Checkpoints

`/srv/anu/checkpoints/mutations/<project>/<transaction_id>/` holds the
original files and `manifest.json` (SHA-256 per file). To restore manually,
copy files back and compare hashes with the manifest.

### Code and docs

Code lives in git and is backed up to GitHub
(`Sadhu2005/Anu-AI-Agent`). Runtime state (`brain/`, `checkpoints/`, `logs/`,
`workspace/`) is **not** in git and exists only on the SD card.

## Pulling Pi Changes into the Workspace

From the Windows workspace (`D:\Anu-AI-Agent`), the `pi` remote reads the Pi's
repository as `anu-agent`:

```powershell
git fetch pi
```
