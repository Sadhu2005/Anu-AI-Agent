#!/usr/bin/env bash
# V1.2 Hermes smokes — run on Pi after filling ~/.hermes/.env manually.
# Does not install LM Studio. Does not touch /srv/anu.
set -euo pipefail
export PATH="${HOME}/.local/bin:${PATH}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
# shellcheck disable=SC1091
set -a
# Prefer HERMES_* then fall back to Hermes-native names already in .env
if [[ -f "$HERMES_HOME/.env" ]]; then
  # shellcheck disable=SC1090
  source "$HERMES_HOME/.env"
fi
set +a

# Mirror HERMES_* into Hermes-native vars if operator only filled HERMES_*.
if [[ -n "${HERMES_OPENROUTER_API_KEY:-}" && -z "${OPENROUTER_API_KEY:-}" ]]; then
  export OPENROUTER_API_KEY="$HERMES_OPENROUTER_API_KEY"
fi
if [[ -n "${HERMES_LMSTUDIO_BASE_URL:-}" ]]; then
  export LM_BASE_URL="$HERMES_LMSTUDIO_BASE_URL"
fi

echo "=== hermes version ==="
hermes --version

echo "=== isolation checks ==="
if test -r /srv/anu/secrets/github-token 2>/dev/null; then
  echo "FAIL: Hermes user can read /srv/anu/secrets"
  exit 1
fi
echo "OK: cannot read /srv/anu/secrets"
if test -r /srv/anu/brain/anu-brain.db 2>/dev/null; then
  echo "FAIL: Hermes user can read brain DB"
  exit 1
fi
echo "OK: cannot read brain DB"

echo "=== blank-slate toolsets ==="
python3 - <<'PY'
import yaml
from pathlib import Path
cfg = yaml.safe_load(Path.home().joinpath(".hermes", "config.yaml").read_text())
cli = (cfg.get("platform_toolsets") or {}).get("cli")
disabled = (cfg.get("agent") or {}).get("disabled_toolsets") or []
print("cli", cli)
assert cli == ["file", "terminal"], cli
for must in ("delegation", "cronjob", "browser", "web"):
    assert must in disabled, must
print("OK blank-slate")
PY

echo "=== OpenRouter smoke (requires OPENROUTER_API_KEY) ==="
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "SKIP OpenRouter: set OPENROUTER_API_KEY or HERMES_OPENROUTER_API_KEY in $HERMES_HOME/.env"
else
  hermes config set model.provider openrouter
  hermes config set model.base_url https://openrouter.ai/api/v1
  hermes config set model.default "${HERMES_OPENROUTER_MODEL:-openrouter/auto}"
  # -z = one-shot non-interactive prompt
  hermes -z "Reply with exactly: OR_SMOKE_OK" 2>&1 | tee /tmp/hermes-or-smoke.log | tail -40
fi

echo "=== LM Studio smoke (requires your LAN server + HERMES_LMSTUDIO_MODEL) ==="
BASE="${LM_BASE_URL:-${HERMES_LMSTUDIO_BASE_URL:-http://192.168.1.13:1234/v1}}"
if [[ -z "${HERMES_LMSTUDIO_MODEL:-}" ]]; then
  echo "SKIP LM Studio: set HERMES_LMSTUDIO_MODEL in $HERMES_HOME/.env and start LM Studio API on the PC"
  echo "  Probe: curl -sS ${BASE}/models"
else
  if ! curl -sS -m 5 "${BASE}/models" >/tmp/hermes-lm-models.json; then
    echo "FAIL: LM Studio not reachable at ${BASE} (start the service on the PC)"
    exit 1
  fi
  hermes config set model.provider lmstudio
  hermes config set model.base_url "$BASE"
  hermes config set model.default "$HERMES_LMSTUDIO_MODEL"
  hermes -z "Reply with exactly: LM_SMOKE_OK" 2>&1 | tee /tmp/hermes-lm-smoke.log | tail -40
fi

echo "=== done ==="
