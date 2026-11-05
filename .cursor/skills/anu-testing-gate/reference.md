# ANU Testing Gate — Reference

## Layer classification heuristics

Use file names and scope to pick layers:

| Signal in test / change | Layer |
|---|---|
| Pure helper, schema parse, config load, classifier | Unit |
| Two+ components wired (scheduler+agent, policy+resources) | Integration |
| Feature path with PASS/FAIL behaviour (goal cycle, mutation, dashboard API) | Functional |
| `run_v*_regression.py` or “all prior suites” | Regression |
| systemd → runtime → guardian → sandbox on real Pi | E2E / production |

Many ANU files are standalone scripts that already mix unit+functional asserts.
Treat a file by **intent** of the change; still run the matching regression pack.

## Portable vs Pi-only

- **Portable (CI / workspace):** packs listed in `.github/workflows/ci.yml`
  (`run_v0_6_2` … `run_v0_9_lab`). Prefer these first.
- **Pi-only:** tests hard-coding `/srv/anu`, live brain, or sandbox-test-project.
  See [docs/TESTING.md](../../../docs/TESTING.md) isolation section.

Live-brain tests (examples historically called out):
`test_task_retry_v0_5_1`, `test_task_execution_context_e2e_v0_5_1`,
`test_functional_failure_rollback_v0_5_1`, and related V0.5.x graph/wake files.
Back up brain first; stop `anu-agent.service` before running them.

Fixture note: several mutation tests need
`sandbox-test-project/app.py` to contain `return (a + b)`. Restore via a task,
not hand-edits that leave production dirty.

## Helpers

| Tool | Use |
|---|---|
| `tools/pi-run-pack.ps1` | Windows → scp `bin/tests/migrations/config` to `/tmp`, run pack with `ANU_BASE` (no sudo) |
| `tools/pi-deploy.sh` | Linux/macOS clone/checkout + evidence JSON |
| `bin/anu-test-matrix-v0.1.py` | Detect/run unit→integration→functional→e2e→regression |
| `tools/check_doc_config_drift.py` | Docs vs config drift (CI job) |

Example pack from Windows:

```powershell
powershell -File tools/pi-run-pack.ps1 -Pack tests/run_v0_9_lab_regression.py
```

## SSH / git on Pi

```bash
Host: anu-vps1947@raspberrypi
Repo: /srv/anu
User for tests: anu-agent
Git as root: PIGIT="sudo git -c safe.directory=/srv/anu -C /srv/anu"
```

Dashboard tunnel (optional after deploy):

```bash
ssh -L 8787:127.0.0.1:8787 anu-vps1947@raspberrypi
```

## Writing a new test

1. `tests/test_<feature>_vX_Y_Z.py`
2. Temp dir + temp SQLite; exit non-zero on fail
3. Mention in `docs/versions/vX.Y.Z.md` **Tests** section
4. Wire into the version’s `run_vX_Y_Z_regression.py` when that pack exists
5. Prefer not to depend on live brain

## Failure triage

| Symptom | Likely cause | Action |
|---|---|---|
| AST / py_compile fail | Syntax | Fix workspace, re-push |
| Pack fails only on Pi | `/srv/anu` paths, permissions, missing deps | Run as `anu-agent`; check `ANU_BASE` |
| Live DB pollution / lock | Service running during tests | Stop service; use `/tmp` pack |
| Fixture assertion fail | `sandbox-test-project` drifted | Restore fixture via task |
| Dashboard 502 / stale | Service not restarted | Restart `anu-dashboard.service` |
| Git dirty on Pi | Local edits on device | Do not overwrite blindly; inspect, then checkout known SHA |

## Minimal smoke after restart

```bash
sudo systemctl show anu-agent.service -p MainPID -p ActiveState -p SubState
sudo journalctl -u anu-agent.service --no-pager -n 100
# expect NORMAL health + scheduler cycle, no traceback spam
```
