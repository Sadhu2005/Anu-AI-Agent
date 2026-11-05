#!/bin/bash
set -euo pipefail
cd /tmp
SRC=/tmp/anu-purge2
sudo sed -i 's/\r$//' "$SRC"/* || true
sudo install -m 0644 "$SRC/anu-goal-service-v0.1.py" /srv/anu/bin/anu-goal-service-v0.1.py
sudo install -m 0644 "$SRC/anu-dashboard-v0.1.py" /srv/anu/bin/anu-dashboard-v0.1.py
sudo sed -i 's/\r$//' /srv/anu/bin/anu-goal-service-v0.1.py /srv/anu/bin/anu-dashboard-v0.1.py
sudo systemctl restart anu-dashboard.service
sleep 1
TOKEN=$(sudo cat /srv/anu/secrets/dashboard-token)
echo "=== before ==="
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/goals | python3 -c 'import sys,json; print([(g["id"],g["status"]) for g in json.load(sys.stdin).get("goals")or[]])'
for GID in 21 19 18; do
  echo "=== delete $GID ==="
  curl -sS -X POST -H "Authorization: Bearer $TOKEN" -H "X-ANU-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"action\":\"delete\",\"goal_id\":$GID}" \
    http://127.0.0.1:8787/api/control | python3 -c 'import sys,json; d=json.load(sys.stdin); print("ok",d.get("ok"),"deleted", (d.get("purge")or{}).get("deleted"),"err",(d.get("purge")or{}).get("error")); print("tasks", (d.get("purge")or{}).get("counts",{}).get("tables",{}).get("tasks")); print("goals", (d.get("purge")or{}).get("counts",{}).get("tables",{}).get("goals"))'
done
echo "=== after API ==="
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/goals | python3 -c 'import sys,json; print([(g["id"],g["status"]) for g in json.load(sys.stdin).get("goals")or[]])'
echo "=== after DB autonomous ==="
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id,status FROM goals WHERE autonomous=1 ORDER BY id DESC;"
