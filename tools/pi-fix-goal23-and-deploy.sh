#!/bin/bash
set -euo pipefail
cd /tmp
SRC=/tmp/anu-g23
sudo sed -i 's/\r$//' "$SRC"/* || true
for f in anu-change-generator-v0.1.py anu-git-workflow-v0.1.py anu-dashboard-v0.1.py; do
  sudo install -m 0644 "$SRC/$f" "/srv/anu/bin/$f"
  sudo sed -i 's/\r$//' "/srv/anu/bin/$f"
done
sudo systemctl restart anu-dashboard.service anu-agent.service
sleep 2
echo "=== generator check ==="
sudo -u anu-agent env ANU_BASE=/srv/anu python3 <<'PY'
from pathlib import Path
import importlib.util, json
p=Path('/srv/anu/bin/anu-change-generator-v0.1.py')
s=importlib.util.spec_from_file_location('g',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
root=Path('/srv/anu/workspace/projects/anu-agent-test')
goal='Add a greet(name) helper that returns Hello, {name}! with unit tests'
res=m.prepare_for_task('anu-agent-test','implement',goal,root=root)
print(res)
doc=json.loads((root/'.anu-changes.json').read_text())
for e in doc:
    print(e['file'], 'DIFF' if e['old']!=e['new'] else 'same')
    if e['old']!=e['new']:
        (root/e['file']).parent.mkdir(parents=True, exist_ok=True)
        (root/e['file']).write_text(e['new'], encoding='utf-8')
        print(' wrote', e['file'])
PY
echo
echo "=== push greet on agent/23-work ==="
sudo -u anu-agent env ANU_BASE=/srv/anu ANU_GITHUB_TOKEN_FILE=/srv/anu/secrets/github-token python3 <<'PY'
import sqlite3, importlib.util, json
from pathlib import Path
p=Path('/srv/anu/bin/anu-post-accept-git-v0.1.py')
s=importlib.util.spec_from_file_location('post',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
db=sqlite3.connect('/srv/anu/brain/anu-brain.db'); db.row_factory=sqlite3.Row
out=m.after_accept(db, goal_id=23, task_id=369, message='goal-23: greet Hello, {name}!', slice_slug='work')
print(json.dumps({k:out.get(k) for k in ('ok','skipped','reason','branch','head','error')}, indent=2))
if out.get('commit'):
    print('commit files via show:')
db.close()
PY
sudo -u anu-agent git -C /srv/anu/workspace/projects/anu-agent-test show --name-only --oneline -1
sudo -u anu-agent cat /srv/anu/workspace/projects/anu-agent-test/src/labapp/__init__.py | head -20
