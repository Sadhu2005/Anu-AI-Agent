#!/bin/bash
# Deploy lab-repo autonomy bins/config/docs to /srv/anu (run on Pi after scp).
set -euo pipefail
SRC="${1:-/tmp/anu-lab-autonomy}"
for f in \
  anu-test-matrix-v0.1.py \
  anu-failure-diagnose-v0.1.py \
  anu-post-accept-git-v0.1.py \
  anu-workspace-lock-v0.1.py \
  anu-verification-v0.1.py \
  anu-git-workflow-v0.1.py \
  anu-dashboard-v0.1.py \
  anu-project-assign-v0.1.py \
  anu-autonomy-controller-v0.1.py \
  anu-change-generator-v0.1.py \
  anu-repo-inspector-v0.1.py \
  anu-autonomy-state-v0.1.py
do
  if [ -f "$SRC/$f" ]; then
    sudo install -m 0644 "$SRC/$f" "/srv/anu/bin/$f"
    sudo sed -i 's/\r$//' "/srv/anu/bin/$f"
  fi
done
if [ -f "$SRC/anu-autonomy.yaml" ]; then
  sudo install -m 0644 "$SRC/anu-autonomy.yaml" /srv/anu/config/anu-autonomy.yaml
fi
if [ -f "$SRC/anu-projects.yaml" ]; then
  sudo install -m 0644 "$SRC/anu-projects.yaml" /srv/anu/config/anu-projects.yaml
fi
sudo mkdir -p /srv/anu/workspace/projects/anu-agent-test
sudo chown -R anu-agent:anu-agent /srv/anu/workspace/projects/anu-agent-test || true
sudo systemctl restart anu-dashboard.service || true
echo "lab autonomy apply ok"
