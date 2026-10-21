#!/bin/bash
# ANU Pi deploy helper — write evidence JSON to stdout.
# Usage (from a machine with ssh/scp to the Pi):
#   bash tools/pi-deploy.sh [ref]
# Default ref: HEAD of current branch.
set -euo pipefail
REF="${1:-HEAD}"
HOST="${ANU_PI_HOST:-anu-vps1947@raspberrypi}"
REMOTE_SCRIPT="/tmp/anu-deploy-$$.sh"
EVIDENCE="/tmp/anu-deploy-evidence-$$.json"

ssh_run() {
  ssh "$HOST" "$@"
}

cat > /tmp/anu-deploy-local.sh <<EOF
#!/bin/bash
set -euo pipefail
cd /tmp
rm -rf anu-deploy-clone
git clone --depth 1 https://github.com/Sadhu2005/Anu-AI-Agent.git anu-deploy-clone 2>/dev/null \\
  || git clone --depth 1 /srv/anu anu-deploy-clone
cd anu-deploy-clone
git fetch --tags origin 2>/dev/null || true
git checkout "$REF" 2>/dev/null || git checkout FETCH_HEAD
python3 -m py_compile bin/anu-autonomy-*.py bin/anu-llm-router-v0.1.py bin/anu-dashboard-v0.1.py 2>/dev/null || true
python3 tests/run_v0_9_0_regression.py
echo "{\\"ok\\": true, \\"ref\\": \\"$REF\\", \\"cwd\\": \\"\$(pwd)\\", \\"head\\": \\"\$(git rev-parse HEAD)\\"}" > "$EVIDENCE"
cat "$EVIDENCE"
EOF

scp /tmp/anu-deploy-local.sh "$HOST:$REMOTE_SCRIPT"
ssh_run "sed -i 's/\r$//' $REMOTE_SCRIPT; bash $REMOTE_SCRIPT"
