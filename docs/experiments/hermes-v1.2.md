# Hermes V1.2 experiment — Pi standalone (manual API keys)

Pinned install on `anu-vps1947@raspberrypi` (outside `/srv/anu`):

| | |
|---|---|
| **Hermes** | `v0.22.7` (stable, local `b200f5bc60`) |
| **Install script SHA256** | `abf338605dae6dd28d101673ac6aed7995c955745db46d47676bc591a7957209` |
| **Install flags** | `--non-interactive --skip-browser --skip-computer-use` |
| **Home** | `/home/anu-vps1947/.hermes` |
| **CLI** | `~/.local/bin/hermes` |

ANU does **not** install LM Studio on the PC. You start that service and paste keys.

## Env vars (fill manually)

On the Pi, edit `~/.hermes/.env` (mode `600`). Template:
[hermes-v1.2-env.example](hermes-v1.2-env.example).

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter cloud smoke (required for OR path) |
| `HERMES_OPENROUTER_API_KEY` | Same key if you prefer the ANU-style name; smoke script mirrors it into `OPENROUTER_API_KEY` |
| `HERMES_OPENROUTER_MODEL` | Default `openrouter/auto` |
| `LM_BASE_URL` / `HERMES_LMSTUDIO_BASE_URL` | LM Studio OpenAI API, default `http://192.168.1.13:1234/v1` |
| `LM_API_KEY` | Only if LM Studio requires a key (often empty) |
| `HERMES_LMSTUDIO_MODEL` | Model id from `GET …/v1/models` once your server is up |

Never place these under `/srv/anu/secrets` for Hermes.

## Apply blank-slate + smoke

```bash
# On Pi (after scp or from a checkout of docs/experiments/)
python3 hermes-v1.2-apply-blank-slate.py
# edit ~/.hermes/.env with your keys / model id
bash hermes-v1.2-smoke.sh
```

Blank-slate policy: `platform_toolsets.cli = [file, terminal]`; delegation /
cron / browser / web / messaging toolsets disabled.

## Isolation

Hermes runs as `anu-vps1947`, not `anu-agent`. Spot-check:

```bash
test -r /srv/anu/secrets/github-token || echo SECRETS_DENIED
test -r /srv/anu/brain/anu-brain.db || echo BRAIN_DENIED
```
