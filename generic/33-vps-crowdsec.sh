#!/usr/bin/env bash
# 33-vps-crowdsec.sh — run ON THE LAPTOP after 30-vps-caddy.sh. Installs CrowdSec + the nftables bouncer on the VPS.
#   [CS_ENROLL_KEY=…] [HOME_PUBLIC_IP=…] 33-vps-crowdsec.sh   (enrolment key from app.crowdsec.net is optional;
#   HOME_PUBLIC_IP allowlists the home connection — a ban there cuts the home server's Nebula link and every site 502s)
. "$(dirname "$0")/lib/common.sh"; load_env
: "${NEBULA_LH_IP:?}"
msg "installing CrowdSec on $NEBULA_LH_IP"
# settings travel inside the piped script (shell-quoted), not on the remote command line where ps/sudo logs would show them
{ printf '%s=%q\n' OVERLAY_CIDR "${OVERLAY_CIDR:-10.42.0.0/24}" DOMAIN "$DOMAIN" HOME_PUBLIC_IP "${HOME_PUBLIC_IP:-}" \
    CS_ENROLL_KEY "${CS_ENROLL_KEY:-}" CS_NAME "${CS_NAME:-heardos-edge}"
  cat "$HERE/vps/crowdsec-install.sh"; } | lh 'bash -s'
msg "CrowdSec running: blocked IPs never reach the sign-in page; check with: cscli decisions list (on the VPS)"
