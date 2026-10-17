#!/usr/bin/env bash
# 33-vps-crowdsec.sh — run ON THE LAPTOP after 30-vps-caddy.sh. Installs CrowdSec + the nftables bouncer on the VPS.
#   [CS_ENROLL_KEY=…] 33-vps-crowdsec.sh      (enrolment key from app.crowdsec.net is optional)
. "$(dirname "$0")/lib/common.sh"; load_env
: "${NEBULA_LH_IP:?}"
msg "installing CrowdSec on $NEBULA_LH_IP"
lh "OVERLAY_CIDR='${OVERLAY_CIDR:-10.42.0.0/24}' CS_ENROLL_KEY='${CS_ENROLL_KEY:-}' CS_NAME='${CS_NAME:-heardos-edge}' bash -s" < "$HERE/vps/crowdsec-install.sh"
msg "CrowdSec running: blocked IPs never reach the sign-in page; check with: cscli decisions list (on the VPS)"
