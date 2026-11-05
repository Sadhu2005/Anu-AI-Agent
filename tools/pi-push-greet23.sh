#!/bin/bash
set -euo pipefail
cd /tmp
sudo install -m 0644 /tmp/anu-g23/anu-change-generator-v0.1.py /srv/anu/bin/anu-change-generator-v0.1.py
sudo sed -i 's/\r$//' /srv/anu/bin/anu-change-generator-v0.1.py
ROOT=/srv/anu/workspace/projects/anu-agent-test
sudo -u anu-agent env ANU_BASE=/srv/anu python3 <<'PY'
from pathlib import Path
import importlib.util, json
p=Path('/srv/anu/bin/anu-change-generator-v0.1.py')
s=importlib.util.spec_from_file_location('g',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
root=Path('/srv/anu/workspace/projects/anu-agent-test')
res=m.generate_greet_changes('anu-agent-test', root=root)
print(res)
doc=json.loads((root/'.anu-changes.json').read_text())
for e in doc:
    path=root/e['file']; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(e['new'], encoding='utf-8')
    print('wrote', e['file'])
PY
sudo -u anu-agent env ANU_BASE=/srv/anu ANU_GITHUB_TOKEN_FILE=/srv/anu/secrets/github-token python3 <<'PY'
import sqlite3, importlib.util, json
from pathlib import Path
p=Path('/srv/anu/bin/anu-post-accept-git-v0.1.py')
s=importlib.util.spec_from_file_location('post',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
db=sqlite3.connect('/srv/anu/brain/anu-brain.db'); db.row_factory=sqlite3.Row
out=m.after_accept(db, goal_id=23, task_id=369, message='goal-23: greet returns Hello, {name}!', slice_slug='work')
print(json.dumps({k:out.get(k) for k in ('ok','skipped','reason','branch','head','error')}, indent=2, default=str))
db.close()
PY
sudo -u anu-agent git -C "$ROOT" show --name-only --oneline -1
sudo -u anu-agent git -C "$ROOT" status -sb
