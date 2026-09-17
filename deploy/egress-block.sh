#!/bin/bash
# Block all outbound connections the bw-synth container might initiate, while
# leaving replies to inbound requests intact. A synthesiser has no reason to
# phone home; if the toolchain is ever exploited, this stops exfiltration.
#
# Scoped strictly to the container's own subnet, so nothing else on the host is
# affected. Idempotent. Docker rebuilds DOCKER-USER on daemon restart, flushing
# custom rules, so install this via the systemd unit to re-assert it.
set -euo pipefail
S="${SUBNET:-172.31.91.0/24}"
iptables -C DOCKER-USER -s "$S" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN 2>/dev/null \
  || iptables -I DOCKER-USER -s "$S" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
iptables -C DOCKER-USER -s "$S" -j DROP 2>/dev/null \
  || iptables -A DOCKER-USER -s "$S" -j DROP
echo "egress block asserted for $S"
