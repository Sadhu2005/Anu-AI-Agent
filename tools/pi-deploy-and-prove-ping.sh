#!/bin/bash
# Deploy missing lab git path + prove ping files land on agent/* remote.
set -euo pipefail
SRC=/tmp/anu-lab-autonomy
sudo sed -i 's/\r$//' "$SRC"/* 2>/dev/null || true
bash /tmp/pi-apply-lab-autonomy.sh "$SRC"
sudo systemctl restart anu-agent.service anu-dashboard.service
sleep 2
echo "=== deployed modules ==="
ls -la /srv/anu/bin/anu-post-accept-git-v0.1.py /srv/anu/bin/anu-test-matrix-v0.1.py /srv/anu/bin/anu-failure-diagnose-v0.1.py
grep -n push_after_accept /srv/anu/config/anu-autonomy.yaml | head
echo
echo "=== generate ping changes ==="
sudo -u anu-agent env ANU_BASE=/srv/anu python3 /srv/anu/bin/anu-change-generator-v0.1.py \
  --project anu-agent-test --role implement \
  --goal 'Add a ping() helper that returns pong with unit tests'
echo
echo "=== apply desired files from generator (direct write for proof) ==="
ROOT=/srv/anu/workspace/projects/anu-agent-test
sudo -u anu-agent env ANU_BASE=/srv/anu python3 - <<'PY'
from pathlib import Path
import importlib.util
p = Path("/srv/anu/bin/anu-change-generator-v0.1.py")
spec = importlib.util.spec_from_file_location("gen", p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
root = Path("/srv/anu/workspace/projects/anu-agent-test")
res = mod.generate_ping_changes("anu-agent-test", root=root)
print(res)
# Materialize new content from .anu-changes.json
import json
doc = json.loads((root / ".anu-changes.json").read_text())
for e in doc:
    path = root / e["file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(e["new"], encoding="utf-8")
    print("wrote", e["file"], "bytes", len(e["new"]))
PY
echo
echo "=== files now ==="
sudo -u anu-agent find "$ROOT/src/labapp" "$ROOT/tests/unit" -type f -name '*.py' | sort
sudo -u anu-agent grep -n "def ping" "$ROOT/src/labapp/ping.py"
echo
echo "=== after_accept push goal 21 ==="
sudo -u anu-agent env ANU_BASE=/srv/anu ANU_GITHUB_TOKEN_FILE=/srv/anu/secrets/github-token \
  python3 - <<'PY'
import sqlite3, importlib.util, json
from pathlib import Path
p = Path("/srv/anu/bin/anu-post-accept-git-v0.1.py")
spec = importlib.util.spec_from_file_location("post", p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
db = sqlite3.connect("/srv/anu/brain/anu-brain.db")
db.row_factory = sqlite3.Row
out = mod.after_accept(
    db, goal_id=21, task_id=361,
    message="goal-21: add ping() helper + unit test",
    slice_slug="ping",
)
print(json.dumps(out, indent=2, default=str))
db.close()
PY
echo
echo "=== local git ==="
sudo -u anu-agent git -C "$ROOT" branch --show-current
sudo -u anu-agent git -C "$ROOT" log --oneline -3
sudo -u anu-agent git -C "$ROOT" status -sb
echo
echo "=== remote heads (agent) ==="
sudo -u anu-agent env ANU_BASE=/srv/anu ANU_GITHUB_TOKEN_FILE=/srv/anu/secrets/github-token \
  python3 - <<'PY'
import os, urllib.parse, subprocess
from pathlib import Path
token = Path("/srv/anu/secrets/github-token").read_text().strip()
q = urllib.parse.quote(token, safe="")
env = os.environ.copy()
env["GIT_CONFIG_COUNT"] = "1"
env["GIT_CONFIG_KEY_0"] = f"url.https://x-access-token:{q}@github.com/.insteadOf"
env["GIT_CONFIG_VALUE_0"] = "https://github.com/"
r = subprocess.run(
    ["git","ls-remote","--heads","origin"],
    cwd="/srv/anu/workspace/projects/anu-agent-test",
    env=env, capture_output=True, text=True,
)
for line in (r.stdout or "").splitlines():
    if "agent/" in line:
        print(line)
PY
