#!/bin/bash
set -euo pipefail
echo "config:"
grep -n push_after_accept /srv/anu/config/anu-autonomy.yaml || echo MISSING
ls -la /srv/anu/bin/anu-post-accept-git-v0.1.py /srv/anu/bin/anu-test-matrix-v0.1.py
echo
echo "verification records goal 21:"
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id, task_id, decision, substr(COALESCE(evidence,''),1,120) FROM task_verifications WHERE goal_id=21 OR task_id IN (359,360,361,362,363) ORDER BY id DESC LIMIT 10;" 2>/dev/null \
|| sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db ".tables" | tr ' ' '\n' | grep -i verif
echo
echo "manual after_accept dry:"
sudo -u anu-agent env ANU_BASE=/srv/anu ANU_GITHUB_TOKEN_FILE=/srv/anu/secrets/github-token \
  python3 /srv/anu/bin/anu-post-accept-git-v0.1.py 2>&1 | head -5 || true
sudo -u anu-agent env ANU_BASE=/srv/anu ANU_GITHUB_TOKEN_FILE=/srv/anu/secrets/github-token \
  python3 - <<'PY'
import sqlite3, importlib.util
from pathlib import Path
p = Path("/srv/anu/bin/anu-post-accept-git-v0.1.py")
spec = importlib.util.spec_from_file_location("post", p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
db = sqlite3.connect("/srv/anu/brain/anu-brain.db")
db.row_factory = sqlite3.Row
out = mod.after_accept(db, goal_id=21, task_id=363, message="manual audit goal 21", skip_matrix=False)
print(out)
db.close()
PY
