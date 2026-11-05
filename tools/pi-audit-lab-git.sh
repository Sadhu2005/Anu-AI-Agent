#!/bin/bash
set -euo pipefail
ROOT=/srv/anu/workspace/projects/anu-agent-test
echo "=== workspace git ==="
sudo -u anu-agent git -C "$ROOT" remote -v
sudo -u anu-agent git -C "$ROOT" branch -vv
sudo -u anu-agent git -C "$ROOT" status -sb
echo
echo "=== recent commits ==="
sudo -u anu-agent git -C "$ROOT" log --oneline -8
echo
echo "=== files under src/ and tests/ ==="
sudo -u anu-agent find "$ROOT/src" "$ROOT/tests" -type f 2>/dev/null | sort || true
echo
echo "=== ping/greet search ==="
sudo -u anu-agent grep -RIn --include='*.py' -E 'def (ping|greet)' "$ROOT" 2>/dev/null || echo "(none)"
echo
echo "=== goal 21 events (git/push) ==="
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id, event_type, substr(COALESCE(message,''),1,80) AS msg, created_at
   FROM agent_events WHERE goal_id=21
   AND (event_type LIKE '%GIT%' OR event_type LIKE '%PUSH%' OR event_type LIKE '%MUTATION%' OR event_type LIKE '%ACCEPT%' OR event_type='GOAL_STATUS_CHANGED')
   ORDER BY id DESC LIMIT 30;"
echo
echo "=== goal 21 tasks ==="
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id, status, title FROM tasks WHERE goal_id=21 ORDER BY id;"
echo
echo "=== remote branch tip (ls-remote) ==="
sudo -u anu-agent env ANU_BASE=/srv/anu ANU_GITHUB_TOKEN_FILE=/srv/anu/secrets/github-token \
  python3 - <<'PY'
import os
from pathlib import Path
import urllib.parse, subprocess
token = Path(os.environ["ANU_GITHUB_TOKEN_FILE"]).read_text().strip()
q = urllib.parse.quote(token, safe="")
env = os.environ.copy()
env["GIT_CONFIG_COUNT"] = "1"
env["GIT_CONFIG_KEY_0"] = f"url.https://x-access-token:{q}@github.com/.insteadOf"
env["GIT_CONFIG_VALUE_0"] = "https://github.com/"
env["GIT_TERMINAL_PROMPT"] = "0"
root = "/srv/anu/workspace/projects/anu-agent-test"
r = subprocess.run(["git","ls-remote","--heads","origin"], cwd=root, env=env, capture_output=True, text=True)
print(r.stdout or r.stderr)
PY
