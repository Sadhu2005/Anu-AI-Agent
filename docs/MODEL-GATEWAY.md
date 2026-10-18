# ANU Model Gateway — Operator Guide

> Intelligence is a **plug-in**. Swap LM Studio ↔ OpenAI ↔ any OpenAI-compatible
> server by changing **base URL + model + API key**. The agent code stays the
> same.

Related: [MODEL-STRATEGY.md](MODEL-STRATEGY.md) · [versions/v0.7.1.md](versions/v0.7.1.md) · ADR-011

---

## 1. Architecture

```text
┌──────────────────────────── Pi ────────────────────────────┐
│  anu-agent.service          PrivateNetwork=yes             │
│       │                                                    │
│       │  request_reasoning (no raw HTTP from agent)        │
│       ▼                                                    │
│  anu-model-gateway.service  narrow network only            │
│       │                                                    │
└───────┼────────────────────────────────────────────────────┘
        │  OpenAI-compatible HTTP
        ▼
   Provider you choose
   • LM Studio on PC (LAN)     base_url = http://PC:1234/v1
   • OpenAI cloud              base_url = https://api.openai.com/v1
   • Groq / Together / …       their OpenAI-compatible /v1 URL
```

The gateway is the **only** ANU component allowed to talk to model providers.

---

## 2. Config file

`config/anu-model-gateway.yaml` (committed defaults; **no secrets**):

```yaml
enabled: true
provider: openai_compatible   # protocol family; do not fork per vendor

# --- switch providers here ---
base_url: "http://127.0.0.1:1234/v1"   # LM Studio default; use PC LAN IP from Pi
model: "meta-llama-3.1-8b-instruct"    # must match provider's model id

# API key is NOT stored here. See section 3.
api_key_env: "ANU_MODEL_API_KEY"

timeout_seconds: 120
max_tokens: 1024
temperature: 0.2

privacy:
  default_class: INTERNAL
  # SECRET class is never sent (enforced in gateway)

budget:
  # Soft caps for cloud spend; LM Studio local = $0
  max_usd_per_request: 0.05
  max_usd_per_day: 1.0
```

### Common mistake (401 malformed token)

| Knob | What goes here | Example |
|---|---|---|
| YAML `base_url` | Server address | `http://192.168.1.13:1234/v1` or `http://127.0.0.1:1234/v1` |
| `ANU_MODEL_API_KEY` | Bearer token only | LM Studio **Manage Tokens** value, or OpenAI `sk-...` |

**Never** put `http://…` in `ANU_MODEL_API_KEY`. The gateway rejects URL-shaped
keys. PowerShell keeps env vars for the session — after a bad value run
`Remove-Item Env:ANU_MODEL_API_KEY` before trying again.

### Switch examples

**A. LM Studio on your PC (recommended for home lab)**

1. In LM Studio: load a model (e.g. `meta-llama-3.1-8b-instruct`).
2. Start the local server; enable **Serve on Network** / bind `0.0.0.0`.
3. On the PC (smoke test), keep YAML `base_url: "http://127.0.0.1:1234/v1"`.
   On the Pi, set:

```yaml
base_url: "http://192.168.1.13:1234/v1"   # your PC's LAN IP
model: "meta-llama-3.1-8b-instruct"
```

4. API key:
   - If **Require Authentication** is ON: Developer → Server Settings →
     **Manage Tokens** → Create Token → copy once into `ANU_MODEL_API_KEY`.
   - If auth is OFF: leave the env unset (no Authorization header).
   - Placeholder strings like `lm-studio` only work when the server does not
     enforce real tokens.

**B. OpenAI (or any cloud OpenAI-compatible API)**

```yaml
base_url: "https://api.openai.com/v1"
model: "gpt-4o-mini"   # or your chosen id
```

Set the real key via credential (section 3). **Only change YAML + key** — no
code change.

**C. Another OpenAI-compatible host**

Same as B: point `base_url` at their `/v1` root, set `model` and key.

---

## 3. API key delivery (never commit keys)

| Wrong | Right |
|---|---|
| Key in git / `config/*.yaml` | Env `ANU_MODEL_API_KEY` or systemd `LoadCredential=` |
| Key under `/srv/anu/secrets` writable by agent | Root-owned file only the gateway user can read |
| Pasting a live token into chat / public issues | Rotate (delete + create) in Manage Tokens |

Pi (later, with systemd):

```ini
# in anu-model-gateway.service
# LoadCredential=model_api_key:/etc/anu/model-api-key
# Environment=ANU_MODEL_API_KEY_FILE=%d/model_api_key
```

Dev / smoke test on PC (PowerShell):

```powershell
$env:ANU_BASE="d:\Anu-AI-Agent"
$env:ANU_MODEL_API_KEY="paste-lm-studio-token-here"
python -B bin/anu-model-gateway-v0.1.py health
python -B bin/anu-model-gateway-v0.1.py chat --prompt "Reply with OK only."
```

---

## 4. Models you already have (LM Studio)

From your LM Studio library, good starting picks for ANU planning:

| Model | Role |
|---|---|
| **meta-llama-3.1-8b-instruct** | Primary — instruction-following, planning |
| **phi-3.1-mini-128k-instruct** | Lighter / longer context experiments |
| **gemma-2-9b-it** / **gemma-2-2b-it** | Alternate instruct models |
| **deepseek-coder-6.7b-kexer** | Code-oriented (later, for code hints) |
| tinyllama / gpt2-small-danish | Too weak for serious planning — skip |

Load **one** model in LM Studio before starting the server; set `model` in YAML
to the exact id LM Studio shows in `/v1/models`.

---

## 5. Smoke test checklist

**On PC (LM Studio running):**

```bash
curl http://127.0.0.1:1234/v1/models
```

**From Pi (LAN to PC LM Studio):**

```bash
curl http://192.168.1.13:1234/v1/models
curl http://192.168.1.13:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ANU_MODEL_API_KEY" \
  -d '{"model":"meta-llama-3.1-8b-instruct","messages":[{"role":"user","content":"ping"}],"max_tokens":32}'
```

**Via ANU gateway CLI (direct HTTP, same as PC smoke):**

```bash
export ANU_BASE=/srv/anu
# Edit config base_url to http://192.168.1.13:1234/v1 on the Pi
export ANU_MODEL_API_KEY_FILE=/etc/anu/model-api-key   # or ANU_MODEL_API_KEY=
python3 -B bin/anu-model-gateway-v0.1.py health
python3 -B bin/anu-model-gateway-v0.1.py chat --prompt "Reply with OK only."
```

**Via Unix socket (agent path, after systemd unit is up):**

```bash
sudo systemctl status anu-model-gateway
sudo -u anu-agent python3 -B /srv/anu/bin/anu-model-client-v0.1.py health
sudo -u anu-agent python3 -B /srv/anu/bin/anu-model-client-v0.1.py chat \
  --prompt "Reply with OK only."
```

---

## 5b. Pi install (V0.7.1 step 2)

1. Pull `v0.7.1-integration` on `/srv/anu`.
2. Set `base_url: "http://192.168.1.13:1234/v1"` in
   `/srv/anu/config/anu-model-gateway.yaml` (PC LAN IP; keep LM Studio running).
3. Create system user + key file + unit (see comments in
   `deploy/systemd/anu-model-gateway.service`).
4. `systemctl enable --now anu-model-gateway`
5. If status shows `200/CHDIR`, the unit WorkingDirectory was wrong — pull latest
   unit (`WorkingDirectory=/srv/anu`) or `mkdir -p /srv/anu` and restart.
6. Confirm socket: `ls -l /run/anu-model/model.sock`
7. Client as `anu-agent` (peercred): `anu-model-client-v0.1.py health`

`anu-agent.service` stays `PrivateNetwork=yes` — it must **not** call
`192.168.1.13` directly; only the gateway process may.

---

## 6. Safety rules (unchanged)

- Model output is **data**, not authority. Plans still go through policy,
  Safe Mutation, sandbox, verification.
- `SECRET` privacy class must never leave the Pi.
- Gateway outage → ANU keeps running on the **template planner** (V0.7.0 path).
- Do not expose LM Studio to the public internet; LAN only.

---

## 7. Implementation status

| Piece | Status |
|---|---|
| Config schema + operator guide | V0.7.1 step 1 |
| `anu-model-gateway-v0.1.py` (OpenAI-compatible + `serve`) | V0.7.1 step 1–2 |
| `anu-model-client-v0.1.py` (AF_UNIX client) | V0.7.1 step 2 |
| systemd + LoadCredential + SO_PEERCRED | V0.7.1 step 2 |
| Planner optional LLM path | V0.7.1 step 3 |
