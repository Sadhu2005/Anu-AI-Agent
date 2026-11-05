#!/bin/bash
set -euo pipefail
cd /tmp
SRC=/tmp/anu-lab-fix
sudo sed -i 's/\r$//' "$SRC"/* || true
for f in anu-change-generator-v0.1.py anu-git-workflow-v0.1.py anu-post-accept-git-v0.1.py; do
  sudo install -m 0644 "$SRC/$f" "/srv/anu/bin/$f"
  sudo sed -i 's/\r$//' "/srv/anu/bin/$f"
done
if [ -f "$SRC/AUTONOMY.md" ]; then
  sudo install -m 0644 "$SRC/AUTONOMY.md" /srv/anu/docs/AUTONOMY.md 2>/dev/null || true
fi
echo "=== detector check ==="
sudo -u anu-agent env ANU_BASE=/srv/anu python3 /srv/anu/bin/anu-change-generator-v0.1.py \
  --project anu-agent-test --role implement \
  --goal 'Build a simple in-memory TodoList with add/list and unit tests'
echo
ROOT=/srv/anu/workspace/projects/anu-agent-test
sudo -u anu-agent env ANU_BASE=/srv/anu python3 - <<'PY'
from pathlib import Path
import importlib.util, json
p = Path("/srv/anu/bin/anu-change-generator-v0.1.py")
spec = importlib.util.spec_from_file_location("gen", p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
root = Path("/srv/anu/workspace/projects/anu-agent-test")
res = mod.generate_todo_list_changes("anu-agent-test", root=root)
print(res)
doc = json.loads((root / ".anu-changes.json").read_text())
for e in doc:
    path = root / e["file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(e["new"], encoding="utf-8")
    print("wrote", e["file"])
PY
echo
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
    db, goal_id=22, task_id=366,
    message="goal-22: add TodoList + unit tests",
    slice_slug="work",
)
print(json.dumps(out, indent=2, default=str)[:2500])
db.close()
PY
echo
echo "=== verify files ==="
sudo -u anu-agent ls -la "$ROOT/todo.py" "$ROOT/test_todo.py"
sudo -u anu-agent git -C "$ROOT" show --stat --name-only HEAD | head -30
sudo -u anu-agent git -C "$ROOT" branch --show-current
