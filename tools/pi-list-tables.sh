#!/bin/bash
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db ".tables"
echo "---"
sudo -u anu-agent sqlite3 /srv/anu/brain/anu-brain.db "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%goal_id%' ORDER BY name;"
