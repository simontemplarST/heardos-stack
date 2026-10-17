#!/usr/bin/env bash
# crowdsec-install.sh — run ON THE VPS as root (30-vps-caddy.sh must have run: Caddy writes /var/log/caddy/access.log).
# CrowdSec agent + nftables firewall bouncer; Caddy access log, sshd and system logs as sources; the Nebula overlay is
# never banned. Env: OVERLAY_CIDR (default 10.42.0.0/24), optional CS_ENROLL_KEY (free console at app.crowdsec.net),
# optional CS_NAME (engine name shown in the console). Idempotent.
set -euo pipefail
OVERLAY_CIDR="${OVERLAY_CIDR:-10.42.0.0/24}"
export DEBIAN_FRONTEND=noninteractive
if ! command -v cscli >/dev/null; then
  curl -fsSL https://install.crowdsec.net | sh >/dev/null
  apt-get -yq install crowdsec crowdsec-firewall-bouncer-nftables >/dev/null
fi
cscli collections install crowdsecurity/caddy crowdsecurity/linux crowdsecurity/sshd crowdsecurity/http-cve crowdsecurity/base-http-scenarios >/dev/null 2>&1 || true
install -d /etc/crowdsec/acquis.d /var/log/caddy
printf 'filenames:\n  - /var/log/caddy/access.log\nlabels:\n  type: caddy\n' > /etc/crowdsec/acquis.d/caddy.yaml
cat > /etc/crowdsec/parsers/s02-enrich/heardos-whitelist.yaml <<X
name: heardos/whitelist
description: "HeardOS: never ban the Nebula overlay"
whitelist:
  reason: "HeardOS overlay"
  cidr:
    - ${OVERLAY_CIDR}
X
systemctl restart crowdsec; sleep 3
# the package's automatic bouncer registration can leave a key the local API rejects ("access forbidden"): (re)register ours
C=/etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml
if ! systemctl restart crowdsec-firewall-bouncer 2>/dev/null || ! sleep 3 || ! systemctl is-active -q crowdsec-firewall-bouncer; then
  cscli bouncers delete heardos-nftables >/dev/null 2>&1 || true
  K=$(cscli bouncers add heardos-nftables -o raw)
  sed -i "s|^api_key:.*|api_key: ${K}|" "$C"; chmod 600 "$C"
  systemctl restart crowdsec-firewall-bouncer; sleep 3
fi
systemctl enable -q crowdsec crowdsec-firewall-bouncer
if [ -n "${CS_ENROLL_KEY:-}" ]; then
  cscli console enroll -e context "$CS_ENROLL_KEY" --name "${CS_NAME:-heardos-edge}" >/dev/null && systemctl restart crowdsec
  echo "enrolled: accept the engine at https://app.crowdsec.net (Security Engines)"
fi
systemctl is-active crowdsec crowdsec-firewall-bouncer | paste -sd' '
