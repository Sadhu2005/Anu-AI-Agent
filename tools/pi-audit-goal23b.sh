#!/bin/bash
cd /tmp
ROOT=/srv/anu/workspace/projects/anu-agent-test
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id,status,title FROM tasks WHERE goal_id=23 ORDER BY id;"
echo "---"
sudo -u anu-agent cat "$ROOT/src/labapp/__init__.py"
echo "---"
sudo -u anu-agent env ANU_BASE=/srv/anu python3 /srv/anu/bin/anu-change-generator-v0.1.py \
  --project anu-agent-test --role implement \
  --goal 'Add a greet(name) helper that returns Hello, {name}! with unit tests' | head -20
grep -n "generate_greet\|wants_greet" /srv/anu/bin/anu-change-generator-v0.1.py | head
