#!/bin/bash
# Run an ANU regression pack on the Pi from a /tmp clone (no sudo / brain needed).
# Usage:
#   bash tools/pi-pack-runner.sh tests/run_v0_9_0_regression.py
set -euo pipefail
PACK="${1:?pack script path required}"
HOST="${ANU_PI_HOST:-anu-vps1947@raspberrypi}"
REMOTE="/tmp/anu-pack-$$.sh"

cat > /tmp/anu-pack-local.sh <<EOF
#!/bin/bash
set -euo pipefail
STAMP=\$(date +%Y%m%d%H%M%S)
DIR=/tmp/anu-pack-\$STAMP
rm -rf "\$DIR"
mkdir -p "\$DIR"
# Prefer syncing from /srv/anu working tree when readable; else shallow clone.
if [ -r /srv/anu/bin/anu-runtime.py ]; then
  cp -a /srv/anu/bin /srv/anu/tests /srv/anu/migrations /srv/anu/config "\$DIR/" 2>/dev/null || true
fi
if [ ! -f "\$DIR/bin/anu-runtime.py" ]; then
  git clone --depth 1 https://github.com/Sadhu2005/Anu-AI-Agent.git "\$DIR/repo"
  cp -a "\$DIR/repo/bin" "\$DIR/repo/tests" "\$DIR/repo/migrations" "\$DIR/repo/config" "\$DIR/"
fi
cd "\$DIR"
export ANU_BASE="\$DIR"
python3 "$PACK"
EOF

# Note: pack path is local-relative; rewrite to run from clone.
PACK_BASE=$(basename "$PACK")
sed -i.bak "s|$PACK|tests/$PACK_BASE|" /tmp/anu-pack-local.sh 2>/dev/null || \
  sed "s|$PACK|tests/$PACK_BASE|" /tmp/anu-pack-local.sh > /tmp/anu-pack-local2.sh && mv /tmp/anu-pack-local2.sh /tmp/anu-pack-local.sh

scp /tmp/anu-pack-local.sh "$HOST:$REMOTE"
# Also scp the pack + modules from the developer machine for freshest code.
scp -r bin tests migrations config "$HOST:/tmp/anu-pack-upload-$$/" 2>/dev/null || true
ssh "$HOST" "sed -i 's/\r$//' $REMOTE; bash $REMOTE"
