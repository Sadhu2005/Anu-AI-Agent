#!/bin/bash
# Smoke-test agent/* push using ANU_GITHUB_TOKEN_FILE (never prints token).
set -euo pipefail
export ANU_BASE=/srv/anu
export ANU_GITHUB_TOKEN_FILE=/srv/anu/secrets/github-token
ROOT=/srv/anu/workspace/projects/anu-agent-test
if [ ! -d "$ROOT/.git" ]; then
  echo "missing_workspace: $ROOT"
  exit 2
fi
cd "$ROOT"
echo "remote:"
git remote -v
echo "branch: $(git branch --show-current)"
git status -sb
python3 /srv/anu/bin/anu-git-workflow-v0.1.py push --project anu-agent-test
