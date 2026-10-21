#!/bin/bash
# Install and start anu-dashboard.service on the Pi (/srv/anu).
set -euo pipefail
cd /srv/anu

UNIT_SRC=/srv/anu/deploy/anu-dashboard.service
UNIT_DST=/etc/systemd/system/anu-dashboard.service
TOKEN_FILE=/srv/anu/secrets/dashboard-token

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "missing $UNIT_SRC — pull v0.9.9 first" >&2
  exit 1
fi

# Token (create once; reuse if present)
if [[ ! -s "$TOKEN_FILE" ]]; then
  umask 077
  openssl rand -hex 24 > "$TOKEN_FILE"
  echo "created new dashboard token"
else
  echo "reusing existing dashboard token"
fi
chown root:anu-agent "$TOKEN_FILE"
chmod 0640 "$TOKEN_FILE"

# Drop-in unit with LoadCredential + logs path (var/ may not exist)
sudo tee "$UNIT_DST" >/dev/null <<'EOF'
[Unit]
Description=ANU Autonomy HTTP Dashboard (v0.1)
After=local-fs.target network.target
Wants=network.target

[Service]
Type=simple
User=anu-agent
Group=anu-agent
WorkingDirectory=/srv/anu/home
ExecStart=/usr/bin/python3 /srv/anu/bin/anu-dashboard-v0.1.py serve --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=5

Environment=ANU_BASE=/srv/anu
LoadCredential=dashboard-token:/srv/anu/secrets/dashboard-token

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
# Dashboard must bind TCP localhost (unlike anu-agent runtime).
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

ReadOnlyPaths=/srv/anu/bin
ReadOnlyPaths=/srv/anu/config
ReadOnlyPaths=/srv/anu/secrets
ReadWritePaths=/srv/anu/brain
ReadWritePaths=/srv/anu/workspace
ReadWritePaths=/srv/anu/logs
ReadWritePaths=/srv/anu/runtime
ReadWritePaths=/srv/anu/home

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now anu-dashboard.service
sleep 1
sudo systemctl --no-pager -l status anu-dashboard.service || true
echo "---"
ss -ltn | grep 8787 || sudo ss -ltn | grep 8787 || true
echo "---"
echo "TOKEN (paste into browser when prompted):"
sudo cat "$TOKEN_FILE"
echo
echo "Keep your SSH tunnel open: ssh -L 8787:127.0.0.1:8787 anu-vps1947@raspberrypi"
echo "Then open http://127.0.0.1:8787/"
