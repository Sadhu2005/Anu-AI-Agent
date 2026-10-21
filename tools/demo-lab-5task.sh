#!/bin/bash
# Operator demo: assign a 5-slice style goal against anu-agent-test.
set -euo pipefail
DB="${ANU_DB:-/srv/anu/brain/anu-brain.db}"
python3 /srv/anu/bin/anu-project-assign-v0.1.py \
  --db "$DB" \
  assign \
  --project-key anu-agent-test \
  --github Sadhu2005/anu-agent-test \
  --goal "Five-slice lab: scaffold module, integration test, repair injected fail, docs, regression green" \
  --objective "Unattended multi-task lab run with matrix evidence and agent/* pushes" \
  --criteria "matrix layers green; pushed agent branch; no human next" \
  --autonomous \
  --expand
echo "Assigned. Watch dashboard or: python3 /srv/anu/bin/anu-autonomy-controller-v0.1.py run-once"
