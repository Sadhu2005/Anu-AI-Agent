#!/bin/bash
set -euo pipefail
cd /tmp
echo "=== bins mtime ==="
ls -la /srv/anu/bin/anu-dashboard-v0.1.py /srv/anu/bin/anu-goal-service-v0.1.py
echo
echo "=== purge_goal present? ==="
grep -n "def purge_goal\|action == \"delete\"\|Hide cancelled" /srv/anu/bin/anu-dashboard-v0.1.py | head
grep -n "def purge_goal" /srv/anu/bin/anu-goal-service-v0.1.py | head
echo
echo "=== goals in DB ==="
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id, status, autonomous, substr(description,1,60) FROM goals ORDER BY id DESC LIMIT 15;"
echo
TOKEN=$(sudo cat /srv/anu/secrets/dashboard-token)
echo "=== API goals before ==="
curl -sS -H "Authorization: Bearer $TOKEN" -H "X-ANU-Token: $TOKEN" \
  http://127.0.0.1:8787/api/goals | python3 -c 'import sys,json; d=json.load(sys.stdin); print([(g["id"],g["status"]) for g in d.get("goals")or[]])'
echo
# pick first completed goal to purge via API
GID=$(sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db \
  "SELECT id FROM goals WHERE autonomous=1 AND status='completed' ORDER BY id DESC LIMIT 1;")
echo "=== POST delete goal $GID ==="
curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "X-ANU-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"action\":\"delete\",\"goal_id\":$GID}" \
  http://127.0.0.1:8787/api/control
echo
echo
echo "=== API goals after ==="
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/goals | python3 -c 'import sys,json; d=json.load(sys.stdin); print([(g["id"],g["status"]) for g in d.get("goals")or[]])'
echo
echo "=== DB after ==="
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id, status FROM goals WHERE id=$GID;"
echo "(empty above means deleted)"
