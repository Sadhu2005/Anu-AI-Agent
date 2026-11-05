#!/bin/bash
set -euo pipefail
cd /tmp
ROOT=/srv/anu/workspace/projects/anu-agent-test
echo "=== goal 23 ==="
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id,status,substr(description,1,70) FROM goals WHERE id=23;"
echo
echo "=== GIT events ==="
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id,event_type,substr(message,1,120) FROM agent_events WHERE goal_id=23 AND event_type LIKE 'GIT%' ORDER BY id;"
echo
echo "=== mutations actual_changed ==="
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db \
  "SELECT id,substr(metadata,1,280) FROM agent_events WHERE goal_id=23 AND event_type='MUTATION_CHECKPOINT' AND metadata LIKE '%actual_changed%' ORDER BY id DESC LIMIT 6;"
echo
echo "=== workspace git ==="
sudo -u anu-agent git -C "$ROOT" status -sb
sudo -u anu-agent git -C "$ROOT" branch --show-current
sudo -u anu-agent grep -n 'Hello' "$ROOT/src/labapp/__init__.py" | head
sudo -u anu-agent head -20 "$ROOT/tests/unit/test_greet.py" 2>/dev/null || echo no test_greet
echo
echo "=== remote agent/23 ==="
sudo -u anu-agent env ANU_GITHUB_TOKEN_FILE=/srv/anu/secrets/github-token python3 - <<'PY'
import os,urllib.parse,subprocess
from pathlib import Path
t=Path('/srv/anu/secrets/github-token').read_text().strip()
q=urllib.parse.quote(t,safe='')
env=os.environ.copy(); env['GIT_CONFIG_COUNT']='1'
env['GIT_CONFIG_KEY_0']=f'url.https://x-access-token:{q}@github.com/.insteadOf'
env['GIT_CONFIG_VALUE_0']='https://github.com/'
r=subprocess.run(['git','ls-remote','--heads','origin','agent/23-work'],cwd='/srv/anu/workspace/projects/anu-agent-test',env=env,capture_output=True,text=True)
print(r.stdout or '(no remote branch)')
PY
