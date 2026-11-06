#!/usr/bin/env bash
# V1.3 denial probes. Host checks always run. Container probes run when
# podman and a local image are available. Does not start ANU services.
set -euo pipefail

JOBS_ROOT="${ANU_HERMES_JOBS_ROOT:-/var/lib/anu-hermes/jobs}"
IMAGE="${ANU_HERMES_PROBE_IMAGE:-docker.io/library/busybox:latest}"
FAIL=0

echo "=== host read denial ==="
if test -r /srv/anu/secrets/github-token 2>/dev/null; then
  echo "FAIL: this user can read /srv/anu/secrets/github-token"
  FAIL=1
else
  echo "OK: cannot read secrets token"
fi
if test -r /srv/anu/brain/anu-brain.db 2>/dev/null; then
  echo "FAIL: this user can read the brain DB"
  FAIL=1
else
  echo "OK: cannot read brain DB"
fi
if test -w /srv/anu 2>/dev/null; then
  echo "FAIL: this user can write /srv/anu"
  FAIL=1
else
  echo "OK: /srv/anu is not writable"
fi

echo "=== podman probe ==="
if ! command -v podman >/dev/null 2>&1; then
  echo "SKIP container: podman not installed (run hermes-v1.3-setup.sh as root)"
  exit "$FAIL"
fi

ROOTLESS="$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || echo unknown)"
echo "rootless=$ROOTLESS"
if [[ "$ROOTLESS" != "true" ]]; then
  echo "FAIL: podman is not rootless for this user"
  FAIL=1
fi

if ! podman image exists "$IMAGE" >/dev/null 2>&1; then
  echo "SKIP container egress: image $IMAGE not present locally"
  echo "  As the Hermes user, with host network only for the pull:"
  echo "  podman pull $IMAGE"
  exit "$FAIL"
fi

PROBE="${JOBS_ROOT}/deny-probe-$$"
mkdir -p "$PROBE"
echo "probe" > "$PROBE/hello.txt"

# Write inside the mounted workspace; no production mount; no network.
if podman run --rm --network=none --memory=256m --cpus=1 \
  --cap-drop=ALL --security-opt=no-new-privileges --pids-limit=64 \
  -v "$PROBE:/workspace:rw" \
  "$IMAGE" sh -c 'echo container-write > /workspace/from-container.txt'; then
  if [[ -f "$PROBE/from-container.txt" ]]; then
    echo "OK: container wrote inside the temp workspace"
  else
    echo "FAIL: container command succeeded but file missing"
    FAIL=1
  fi
else
  echo "FAIL: container could not write the temp workspace"
  FAIL=1
fi

# Secrets path is not mounted. Listing it inside the container must fail.
if podman run --rm --network=none --memory=128m --cpus=1 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  "$IMAGE" sh -c 'test -r /srv/anu/secrets/github-token'; then
  echo "FAIL: container can read /srv/anu/secrets"
  FAIL=1
else
  echo "OK: container cannot read secrets path"
fi

# Egress from the code container must fail (--network=none).
if podman run --rm --network=none --memory=128m --cpus=1 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  "$IMAGE" sh -c 'wget -q -O- --timeout=3 http://1.1.1.1/ >/dev/null 2>&1 || wget -q -O- --timeout=3 http://example.com/ >/dev/null 2>&1'; then
  echo "FAIL: container reached the network"
  FAIL=1
else
  echo "OK: container egress denied"
fi

rm -rf "$PROBE"
if [[ "$FAIL" -ne 0 ]]; then
  echo "DENY CHECKS FAILED"
  exit 1
fi
echo "DENY CHECKS OK"
