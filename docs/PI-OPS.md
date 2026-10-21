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

Dashboard binds `127.0.0.1:8787` only:

```bash
ssh -L 8787:127.0.0.1:8787 anu-vps1947@raspberrypi
```

Then open `http://127.0.0.1:8787/` with `ANU_DASHBOARD_TOKEN`.

## Model gateway on Pi

`config/anu-model-gateway.yaml` on the Pi points `base_url` at the PC LAN LM Studio
(e.g. `http://192.168.1.13:1234/v1`). That local edit must be backed up before
resets (`/tmp/anu-model-gateway.yaml.bak.*`).

Multi-provider failover is configured in `config/anu-llm-providers.yaml`
(keys via env / credential files, never in git).
