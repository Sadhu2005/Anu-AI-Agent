#!/bin/bash
cd /tmp
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db <<'SQL'
PRAGMA foreign_keys=ON;
SELECT name, sql FROM sqlite_master WHERE type='table' AND sql LIKE '%REFERENCES%goals%';
SELECT name, sql FROM sqlite_master WHERE type='table' AND sql LIKE '%REFERENCES%tasks%';
SQL
echo "=== leftover refs for goal 21 ==="
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db <<'SQL'
SELECT 'tasks', count(*) FROM tasks WHERE goal_id=21;
SELECT 'agent_events', count(*) FROM agent_events WHERE goal_id=21;
SELECT id FROM tasks WHERE goal_id=21 LIMIT 20;
SQL
# find any table with goal_id still holding 21
for t in $(sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db "SELECT name FROM sqlite_master WHERE type='table'"); do
  cols=$(sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db "PRAGMA table_info($t);" | awk -F'|' '{print $2}')
  echo "$cols" | grep -qx 'goal_id' || echo "$cols" | grep -q '^goal_id$' || echo "$cols" | tr '\n' ' ' | grep -q ' goal_id' || continue
  # has goal_id column
  n=$(sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db "SELECT count(*) FROM $t WHERE goal_id=21;" 2>/dev/null || echo err)
  if [ "$n" != "0" ] && [ "$n" != "err" ]; then
    echo "REF $t goal_id=21 count=$n"
  fi
done
# task_id refs
for tid in $(sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db "SELECT id FROM tasks WHERE goal_id=21;"); do
  echo "task $tid refs:"
  for t in $(sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db "SELECT name FROM sqlite_master WHERE type='table'"); do
    cols=$(sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db "PRAGMA table_info($t);")
    echo "$cols" | grep -q '|task_id|' || continue
    n=$(sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db "SELECT count(*) FROM $t WHERE task_id=$tid;" 2>/dev/null || echo 0)
    if [ "$n" != "0" ]; then echo "  $t=$n"; fi
  done
done
