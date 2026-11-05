#!/bin/bash
set -euo pipefail
cd /tmp
SRC=/tmp/anu-purge
sudo sed -i 's/\r$//' "$SRC"/* || true
sudo install -m 0644 "$SRC/anu-goal-service-v0.1.py" /srv/anu/bin/anu-goal-service-v0.1.py
sudo install -m 0644 "$SRC/anu-dashboard-v0.1.py" /srv/anu/bin/anu-dashboard-v0.1.py
sudo sed -i 's/\r$//' /srv/anu/bin/anu-goal-service-v0.1.py /srv/anu/bin/anu-dashboard-v0.1.py
sudo systemctl restart anu-dashboard.service
sleep 1
systemctl is-active anu-dashboard.service
echo "=== purge cancelled leftovers (20,22 if still cancelled) ==="
sudo -u anu-agent env ANU_BASE=/srv/anu python3 - <<'PY'
import sqlite3, importlib.util
from pathlib import Path
p = Path("/srv/anu/bin/anu-goal-service-v0.1.py")
spec = importlib.util.spec_from_file_location("gs", p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
db = sqlite3.connect("/srv/anu/brain/anu-brain.db")
db.row_factory = sqlite3.Row
rows = db.execute(
    "SELECT id, status FROM goals WHERE LOWER(status)='cancelled' ORDER BY id"
).fetchall()
print("cancelled before:", [dict(r) for r in rows])
for r in rows:
    out = mod.purge_goal(db, int(r["id"]), by_who="deploy_cleanup")
    print("purged", r["id"], out.get("ok"), out.get("counts", {}).get("tasks"))
left = db.execute(
    "SELECT id, status, substr(description,1,50) FROM goals ORDER BY id DESC LIMIT 10"
).fetchall()
print("goals after:")
for r in left:
    print(tuple(r))
db.close()
PY
TOKEN=$(sudo cat /srv/anu/secrets/dashboard-token)
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/goals > /tmp/g.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/g.json"))
print("api goals:", [(g["id"], g["status"]) for g in d.get("goals") or []])
PY
