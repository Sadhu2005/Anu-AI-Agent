#!/usr/bin/env bash
# V1.3 host setup for an isolated Hermes worker on the Pi.
# Run as root (sudo). Does not edit anu-agent.service or Hermes API keys.
set -euo pipefail

USER_NAME="${ANU_HERMES_USER:-anu-hermes}"
JOBS_ROOT="${ANU_HERMES_JOBS_ROOT:-/var/lib/anu-hermes/jobs}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run as root: sudo bash docs/experiments/hermes-v1.3-setup.sh"
  exit 1
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$USER_NAME"
  echo "created user $USER_NAME"
else
  echo "user $USER_NAME already exists"
fi

if ! command -v podman >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y podman slirp4netns fuse-overlayfs
fi

# Next free subuid/subgid block. Do not reuse another user's range
# (anu-vps1947 already owns 100000:65536 on the lab Pi).
next_subid() {
  local file="$1"
  local max=100000
  local start count end
  if [[ ! -f "$file" ]]; then
    echo "$max"
    return
  fi
  while IFS=: read -r _name start count; do
    [[ "${start:-}" =~ ^[0-9]+$ && "${count:-}" =~ ^[0-9]+$ ]] || continue
    end=$((start + count))
    if (( end > max )); then
      max=$end
    fi
  done < "$file"
  echo "$max"
}

if ! grep -q "^${USER_NAME}:" /etc/subuid 2>/dev/null; then
  uid_start="$(next_subid /etc/subuid)"
  gid_start="$(next_subid /etc/subgid)"
  usermod \
    --add-subuids "${uid_start}-$((uid_start + 65535))" \
    --add-subgids "${gid_start}-$((gid_start + 65535))" \
    "$USER_NAME"
  echo "subids for $USER_NAME start at uid ${uid_start} gid ${gid_start}"
fi

loginctl enable-linger "$USER_NAME"
systemctl start "user@$(id -u "$USER_NAME").service" || true

install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$JOBS_ROOT"
# Production tree stays owned by anu-agent; Hermes user must not gain write.
if [[ -d /srv/anu ]]; then
  # Best-effort: do not chmod the whole tree. Only ensure secrets stay unreadable
  # when they already exist and are not world-readable.
  if [[ -e /srv/anu/secrets ]]; then
    chmod o-rwx /srv/anu/secrets || true
  fi
fi

echo "OK anu-hermes user + jobs root $JOBS_ROOT"
echo "Podman: $(podman --version)"
echo "Next (from /tmp; sudo -u fails inside /home/anu-vps1947):"
echo "  sudo bash -c 'cd /tmp && runuser -u $USER_NAME -- env XDG_RUNTIME_DIR=/run/user/$(id -u $USER_NAME) podman info'"
echo "  python3 docs/experiments/hermes-v1.3-isolation.py apply --hermes-home /home/$USER_NAME/.hermes"
