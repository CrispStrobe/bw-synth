#!/bin/bash
# Reproduce the hardened bw-synth container. Run on a host with Docker.
#
# The security posture is the point, not decoration: this service runs a
# toolchain over arbitrary Verilog from the internet, so the workload is
# confined to a non-root, read-only, capability-less, EGRESS-BLOCKED container
# published only on loopback behind a reverse proxy.
#
# Egress blocking is NOT expressed here: a --network flag cannot both publish a
# port and cut egress (an --internal network has no gateway, so published ports
# stop working). It is done at the host firewall, scoped to this container's
# subnet; see deploy/egress-block.sh.
set -euo pipefail

IMAGE="${IMAGE:-bwsynth:latest}"
NAME="${NAME:-bwsynth}"
SUBNET="${SUBNET:-172.31.91.0/24}"
IP="${IP:-172.31.91.10}"
PORT="${PORT:-8091}"
NET="${NET:-bwsynth-net}"

docker network inspect "$NET" >/dev/null 2>&1 || docker network create --subnet "$SUBNET" "$NET"
docker rm -f "$NAME" 2>/dev/null || true
docker run -d --name "$NAME" --restart unless-stopped \
  --user 10001:10001 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit 256 \
  --memory 2g --memory-swap 2g \
  --cpus 1.5 \
  --tmpfs /tmp:rw,nosuid,size=768m \
  --network "$NET" --ip "$IP" \
  -p "127.0.0.1:${PORT}:8091" \
  "$IMAGE"
echo "started $NAME on 127.0.0.1:${PORT}; now apply deploy/egress-block.sh and put a reverse proxy in front."
