#!/bin/bash
set -euo pipefail
sudo sed -i 's/\r$//' /tmp/anu-autonomy-controller-v0.1.py
sudo install -m 0644 /tmp/anu-autonomy-controller-v0.1.py /srv/anu/bin/anu-autonomy-controller-v0.1.py
sudo -u anu-agent env ANU_BASE=/srv/anu python3 /srv/anu/bin/anu-autonomy-controller-v0.1.py \
  --db /srv/anu/brain/anu-brain.db resume --goal 20
# blocked -> active if needed
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db \
  "UPDATE goals SET status='active' WHERE id=20 AND status='blocked';"
sudo -u anu-agent env ANU_BASE=/srv/anu python3 /srv/anu/bin/anu-autonomy-controller-v0.1.py \
  --db /srv/anu/brain/anu-brain.db run-once --goal 20
echo "goal:"
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id, status FROM goals WHERE id=20;"
echo "control:"
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT state, substr(COALESCE(reason,''),1,100) AS reason FROM agent_control WHERE goal_id=20;"
sudo systemctl restart anu-agent.service
