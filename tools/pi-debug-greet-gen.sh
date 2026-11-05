#!/bin/bash
cd /tmp
grep -n 'return f' /srv/anu/bin/anu-change-generator-v0.1.py | head
sudo -u anu-agent env ANU_BASE=/srv/anu python3 <<'PY'
from pathlib import Path
import importlib.util, json
p = Path("/srv/anu/bin/anu-change-generator-v0.1.py")
spec = importlib.util.spec_from_file_location("g", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
goal = 'Add a greet(name) helper that returns Hello, {name}! with unit tests'
print("wants_greet", m.wants_greet_goal(goal))
print("wants_todo", m.wants_todo_goal(goal))
print("wants_ping", m.wants_ping_goal(goal))
root = Path("/srv/anu/workspace/projects/anu-agent-test")
res = m.prepare_for_task("anu-agent-test", "implement", goal, root=root)
print("prepare", res)
doc = json.loads((root/".anu-changes.json").read_text())
for e in doc:
    print("FILE", e["file"], "same" if e["old"]==e["new"] else "DIFF", "old_tail", repr(e["old"][-60:]), "new_tail", repr(e["new"][-60:]))
PY
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db \
  "SELECT COUNT(*) AS n FROM tasks WHERE goal_id=23;
   SELECT id,event_type,substr(message,1,100) FROM agent_events WHERE goal_id=23 ORDER BY id LIMIT 25;"
