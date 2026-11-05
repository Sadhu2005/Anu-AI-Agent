#!/bin/bash
set -euo pipefail
TOKEN=$(sudo cat /srv/anu/secrets/dashboard-token)
curl -sS -H "Authorization: Bearer $TOKEN" \
  -H "X-ANU-Token: $TOKEN" \
  http://127.0.0.1:8787/api/goals > /tmp/anu-goals.json
python3 - <<'PY'
import json
d = json.load(open("/tmp/anu-goals.json"))
gs = d.get("goals") or []
print("ok", d.get("ok"), "count", len(gs))
for g in gs[:6]:
    print(g.get("id"), g.get("status"), "needs_help=", g.get("needs_help"), "control=", g.get("control"))
PY
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/health; echo
grep -n "Pause goal\|Delete goal\|help-banner\|btn-goal-pause" /srv/anu/bin/anu-dashboard-v0.1.py | head
