# ANU Pi Operations (SSH)

ANU runs on the Raspberry Pi at `/srv/anu` as user `anu-agent`.
Developers connect as `anu-vps1947@raspberrypi`.

## Important: this is not the “Pi coding agent” product

ANU is our own agent. The Pi is just the host.

## SSH basics

```bash
ssh anu-vps1947@raspberrypi
```

Windows PowerShell notes:

- Do **not** use `&&` between commands; use `;` or separate statements.
- Do **not** nest `\"` inside double-quoted remote commands — PowerShell mangles them.
- Prefer: write a temp `.sh` → `scp` → `ssh … "bash /tmp/x.sh"` (and `sed -i 's/\r$//'` for CRLF).

Reusable helpers:

- [tools/pi-run-pack.ps1](../tools/pi-run-pack.ps1) — upload bin/tests/config and run a regression pack in `/tmp` (no sudo).
- [tools/pi-deploy.sh](../tools/pi-deploy.sh) — Linux/macOS deploy+evidence helper.

## Brain DB permissions

| Path | Typical owner | SSH user access |
|---|---|---|
| `/srv/anu/brain/anu-brain.db` | `anu-agent` | **denied** without sudo |
| `/srv/anu/config` | readable | OK |
| `/srv/anu` git tree | often root/`anu-agent` | read OK; write may need sudo |

Live goal/task queries need `sudo` (password) or a helper that runs as `anu-agent`.
Regression packs should use a throwaway DB under `/tmp/anu-pack-…` with `ANU_BASE` set.

## Detached HEAD at a release tag

Production checkout often sits at `v0.8.13` (detached). Before agent work:

```bash
git status
git branch --show-current
# only if tag exists and branch name is free; preserve dirty work first
git switch -c agent/continue-v0.8.13 v0.8.13
```

Local development uses `v0.9.0-integration` (and later `v0.9.x-integration`) branched from that line.

## Dashboard tunnel

Dashboard binds `127.0.0.1:8787` only (`anu-dashboard.service`):

```bash
ssh -L 8787:127.0.0.1:8787 anu-vps1947@raspberrypi
```

Then open `http://127.0.0.1:8787/` with `ANU_DASHBOARD_TOKEN`.

### Tabs (V0.9.10)

Overview · Active Goal · Runtime · Approvals · Assign — System gauges stay on Overview. Full map: [AUTONOMY.md](AUTONOMY.md). Backlog of missing panels: [versions/v0.9.10.md](versions/v0.9.10.md).

### Restart after deploy

```bash
sudo systemctl restart anu-dashboard.service
sudo systemctl --no-pager -l status anu-dashboard.service
curl -sS -H "Authorization: Bearer $ANU_DASHBOARD_TOKEN" http://127.0.0.1:8787/api/health
```

Install (once): `bash tools/pi-install-dashboard.sh` on the Pi under `/srv/anu`.

### Lab workspace + GitHub token

Lab push uses a systemd drop-in (`anu-agent.service.d/lab-github.conf`) that
enables outbound TCP (`PrivateNetwork=no` + `AF_INET`/`AF_INET6`) so HTTPS
git works. Destination remains `agent/*` only; PAT lives at
`/srv/anu/secrets/github-token` (`ANU_GITHUB_TOKEN_FILE`). Never commit the
token. Without the drop-in/token, pushes are skipped — do not treat that as
a silent success.

```bash
sudo mkdir -p /srv/anu/workspace/projects/anu-agent-test
sudo chown anu-agent:anu-agent /srv/anu/workspace/projects/anu-agent-test
# Token scopes for clone/push agent/* (never commit the token):
#   contents:read/write on allow-listed repos; no admin; no force-push needed
# Prefer: ANU_GITHUB_TOKEN_FILE=/srv/anu/secrets/github-token
```

Demo assign: `bash tools/demo-lab-5task.sh` (on Pi after deploy).

## Model gateway on Pi

`config/anu-model-gateway.yaml` and `config/anu-llm-providers.yaml` point the
lab at Ollama on Ubuntu anu-server: `http://192.168.1.13:11434/v1`, model
`qwen2.5-coder:7b`, no API key. Back up those files before resets
(`/tmp/anu-model-gateway.yaml.bak.*`).

Multi-provider failover is configured in `config/anu-llm-providers.yaml`
(keys via env / credential files, never in git).

## Hermes worker unit (V1.4)

`anu-hermes.service` is installed from
[deploy/systemd/anu-hermes.service](../deploy/systemd/anu-hermes.service)
and is **disabled**. It is a oneshot check, not a coding job.

| | |
|---|---|
| User / group | `anu-hermes` |
| Working directory | `/var/lib/anu-hermes` |
| Start | `/usr/bin/python3 /srv/anu/bin/anu-hermes-worker-v0.1.py --check` |
| Boot | not enabled |
| Credentials | none. No `Environment=`, no `LoadCredential`, no `/srv/anu/secrets` |

Checked on the Pi at 2026-11-06 04:56:50 UTC while `/srv/anu` was detached at
`a429356`:

```text
{"ok": true, "execute": false, "spawn": false}
anu-hermes.service: Deactivated successfully.
```

`inactive (dead)` after that line is the oneshot finishing. `anu-agent` and
`anu-model-gateway` were active. The gateway stayed on
`http://192.168.1.13:11434/v1`, model `qwen2.5-coder:7b`.

Do not `systemctl enable` this unit. Do not change `ExecStart` to a live
Hermes job. `execute` stays false in `config/anu-hermes.yaml`.

The previous production tree is git stash `pre-v1.4-checkout` on
`v0.9.0-integration`. Leave it. Applying it would put those edits back on
top of the V1.4 checkout. CI commit `59ffacb` only adds the Actions layer
jobs; the Pi was not moved to that commit for this check.
