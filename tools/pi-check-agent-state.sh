#!/bin/bash
set -euo pipefail
echo "services:"
systemctl is-active anu-agent.service anu-dashboard.service || true
echo
echo "schema sample:"
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db "PRAGMA table_info(goals);"
echo "---"
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db "PRAGMA table_info(tasks);"
echo
echo "recent goals:"
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db "SELECT * FROM goals ORDER BY id DESC LIMIT 3;"
echo
echo "open tasks:"
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db "SELECT * FROM tasks WHERE status NOT IN ('completed','failed','cancelled','exhausted') ORDER BY id DESC LIMIT 8;"
echo
echo "workspace:"
if sudo -u anu-agent test -d /srv/anu/workspace/projects/anu-agent-test/.git; then
  sudo -u anu-agent git -C /srv/anu/workspace/projects/anu-agent-test branch --show-current
  sudo -u anu-agent git -C /srv/anu/workspace/projects/anu-agent-test status -sb
else
  echo missing
fi
