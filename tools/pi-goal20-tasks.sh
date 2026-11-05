#!/bin/bash
sudo -u anu-agent sqlite3 -header -column /srv/anu/brain/anu-brain.db \
  "SELECT id, status, title FROM tasks WHERE goal_id=20 ORDER BY id;"
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db \
  "SELECT state, reason FROM agent_control WHERE goal_id=20 OR (scope='global' AND goal_id IS NULL);"
