#!/bin/bash
set -euo pipefail
echo "=== GIT_PUSH events any goal recent ==="
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id, goal_id, event_type, substr(COALESCE(message,''),1,90) AS msg, created_at
   FROM agent_events
   WHERE event_type IN ('GIT_PUSH_OK','GIT_PUSH_FAIL','MATRIX_FAIL')
   ORDER BY id DESC LIMIT 20;"
echo
echo "=== mutation payloads goal 21 (file paths) ==="
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db \
  "SELECT id, event_type, substr(COALESCE(metadata,''),1,500) FROM agent_events
   WHERE goal_id=21 AND event_type LIKE 'MUTATION%' ORDER BY id DESC LIMIT 8;" | head -c 8000
echo
echo "=== __init__.py and counter ==="
sudo -u anu-agent sed -n '1,80p' /srv/anu/workspace/projects/anu-agent-test/src/labapp/__init__.py
echo "---"
sudo -u anu-agent sed -n '1,40p' /srv/anu/workspace/projects/anu-agent-test/src/labapp/counter.py
echo
echo "=== commit e1f58bf files ==="
sudo -u anu-agent git -C /srv/anu/workspace/projects/anu-agent-test show --stat --name-only e1f58bf
echo
echo "=== autonomy push_after_accept ==="
sudo grep -n "push_after_accept\|post.accept" /srv/anu/config/anu-autonomy.yaml || true
grep -n "after_accept\|post_accept" /srv/anu/bin/anu-verification-v0.1.py | head
