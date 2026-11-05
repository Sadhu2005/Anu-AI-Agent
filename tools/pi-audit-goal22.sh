#!/bin/bash
set -euo pipefail
ROOT=/srv/anu/workspace/projects/anu-agent-test
cd /tmp
echo "=== todo files on disk ==="
sudo -u anu-agent ls -la "$ROOT/todo.py" "$ROOT/test_todo.py" 2>&1 || true
sudo -u anu-agent find "$ROOT" -maxdepth 3 -name 'todo*.py' -o -name 'test_todo.py' 2>/dev/null | head
echo
echo "=== .anu contents ==="
sudo -u anu-agent ls -la "$ROOT/.anu" 2>&1 || true
echo
echo "=== git log agent/22-work ==="
sudo -u anu-agent git -C "$ROOT" log --oneline agent/22-work -5 2>/dev/null || sudo -u anu-agent git -C "$ROOT" log --oneline -5
echo
echo "=== files in latest commits ==="
sudo -u anu-agent git -C "$ROOT" show --stat --name-only HEAD
echo "---"
sudo -u anu-agent git -C "$ROOT" show --stat --name-only HEAD~1 2>/dev/null | head -40
echo
echo "=== goal 22 mutations (files) ==="
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db \
  "SELECT id, event_type, substr(metadata,1,350) FROM agent_events
   WHERE goal_id=22 AND event_type LIKE 'MUTATION%'
   ORDER BY id DESC LIMIT 12;"
echo
echo "=== GIT_PUSH goal 22 ==="
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id, event_type, substr(message,1,100), created_at FROM agent_events
   WHERE goal_id=22 AND event_type LIKE 'GIT%' ORDER BY id;"
echo
echo "=== change generator dry-run todo ==="
sudo -u anu-agent env ANU_BASE=/srv/anu python3 /srv/anu/bin/anu-change-generator-v0.1.py \
  --project anu-agent-test --role implement \
  --goal 'Build a simple in-memory TodoList with add/list and unit tests'
echo
echo "=== .anu-changes.json head ==="
sudo -u anu-agent head -c 800 "$ROOT/.anu-changes.json" 2>/dev/null || echo missing
