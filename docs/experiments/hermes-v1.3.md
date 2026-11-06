# Hermes V1.3 — isolated execution environment

Dedicated non-root user, rootless Podman, and a disposable job directory.
The Hermes **host** process calls Ollama at `http://192.168.1.13:11434/v1`
(`qwen2.5-coder:7b`). The **code container** has no network, no production
mount, and no Docker socket.

ANU `anu-agent.service` is not changed.

## Layout

| | |
|---|---|
| User | `anu-hermes` (system, nologin) |
| Jobs | `/var/lib/anu-hermes/jobs/<id>/` |
| Runtime | rootless `podman` via `HERMES_DOCKER_BINARY=podman` |
| Limits | 1 CPU, 2048 MB RAM, 8192 MB disk |
| Network in container | `terminal.docker_network: false` (`--network=none`) |

## One-time setup (Pi, root)

```bash
sudo bash docs/experiments/hermes-v1.3-setup.sh
```

That creates `anu-hermes`, installs Podman if missing, enables linger, and
creates the jobs directory. It does not copy API keys and does not restart
`anu-agent`.

## Apply Hermes terminal policy

Run as the user that owns `HERMES_HOME` (today `anu-vps1947` until the
worker home is moved; target is `anu-hermes`):

```bash
python3 docs/experiments/hermes-v1.3-isolation.py apply --hermes-home "$HOME/.hermes"
python3 docs/experiments/hermes-v1.3-isolation.py prepare \
  --jobs-root /var/lib/anu-hermes/jobs --job-id demo1 --source /path/to/snapshot
```

`prepare` refuses a jobs root under `/srv/anu` and refuses to copy
`/srv/anu/secrets` or `/srv/anu/brain`. Snapshots skip `.env`, `secrets/`,
and private key filenames. `dispose` deletes a job only when
`.anu-hermes-job.json` is present.

## Denial checks

`sudo -u anu-hermes` from `/home/anu-vps1947` fails because that home is not
searchable by `anu-hermes`. Run from `/tmp` with `runuser`:

```bash
cd /tmp
UIDN=$(id -u anu-hermes)
sudo bash -c 'cd /tmp && runuser -u anu-hermes -- env XDG_RUNTIME_DIR="/run/user/'"$UIDN"'" bash /tmp/hermes-v1.3-deny-checks.sh'
```

Host checks (secrets, brain, `/srv/anu` not writable) always run. Container
write / secrets / egress probes run when `podman` and
`docker.io/library/busybox:latest` are already local. Pull that image once
on the host network; do not give the code container a route to pull it.

## Skill set (`anu-hermes`)

Pinned CLI `v0.22.7` lives in `/home/anu-hermes` (not a link into
`anu-vps1947`). Apply the locked tool set after install:

```bash
python3 docs/experiments/hermes-v1.3-apply-skills.py \
  --hermes-home /home/anu-hermes/.hermes
```

That write enables only `file` and `terminal`, disables `skills` plus
delegation, cron, browser, web, and computer use, sets
`terminal.docker_network: false`, and points the model at Ollama. The API
key in that file is the placeholder `ollama`. The model window on
anu-server is 32768 tokens. Hermes is set to `lmstudio` with
`context_length` and `ollama_num_ctx` at 21299, which is 65% of that window.

Install from `/tmp` with `runuser` (see the version evidence for the exact
command). `sudo -u anu-hermes` from `/home/anu-vps1947` cannot chdir.

## Still operator-owned

- Filling OpenRouter / LM Studio keys (optional V1.2 cloud smokes)
- ANU job API (V1.4)
