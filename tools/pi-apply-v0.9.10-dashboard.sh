#!/bin/bash
# Apply V0.9.10 dashboard files already staged in /tmp/anu-v0910
set -euo pipefail
SRC=/tmp/anu-v0910
sudo install -m 0644 "$SRC/anu-dashboard-v0.1.py" /srv/anu/bin/anu-dashboard-v0.1.py
sudo install -m 0644 "$SRC/test_dashboard_v0_9_10.py" /srv/anu/tests/test_dashboard_v0_9_10.py
sudo mkdir -p /srv/anu/docs/versions
sudo install -m 0644 "$SRC/v0.9.10.md" /srv/anu/docs/versions/v0.9.10.md
sudo install -m 0644 "$SRC/AUTONOMY.md" /srv/anu/docs/AUTONOMY.md
sudo install -m 0644 "$SRC/PI-OPS.md" /srv/anu/docs/PI-OPS.md
sudo sed -i 's/\r$//' /srv/anu/bin/anu-dashboard-v0.1.py /srv/anu/tests/test_dashboard_v0_9_10.py
PYTHONDONTWRITEBYTECODE=1 python3 -c "import ast; ast.parse(open('/srv/anu/bin/anu-dashboard-v0.1.py',encoding='utf-8').read()); print('syntax_ok')"
cd /srv/anu
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_dashboard_v0_9_10.py' -v
sudo systemctl restart anu-dashboard.service
sleep 1
sudo systemctl --no-pager -l is-active anu-dashboard.service
TOKEN=""
if [ -f /srv/anu/secrets/dashboard-token ]; then
  TOKEN=$(sudo cat /srv/anu/secrets/dashboard-token)
fi
curl -sS -o /tmp/anu-dash-health.json -w "health_http=%{http_code}\n" -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8787/api/health
curl -sS -o /tmp/anu-dash-index.html -w "index_http=%{http_code}\n" http://127.0.0.1:8787/
echo -n "tab_markers="
grep -c 'data-tab=' /tmp/anu-dash-index.html || echo 0
cat /tmp/anu-dash-health.json
echo
echo "v0.9.10 dashboard apply ok"
