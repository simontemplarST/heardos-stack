#!/usr/bin/env bash
# Run ON the lighthouse as root. CrowdSec agent + nftables firewall bouncer; Caddy JSON access log as the main source.
#   bash crowdsec-lighthouse.sh                 install / re-apply
#   CS_ENROLL_KEY=<key> bash crowdsec-lighthouse.sh   also enrol in the free CrowdSec console
set -euo pipefail
F=/etc/caddy/Caddyfile
# 1) Caddy access log (JSON, rolled) for both site blocks
if ! grep -q '^(accesslog)' $F; then
  cp -a $F $F.bak-20261017-crowdsec
  python3 - "$F" <<'P'
import sys, re
p=sys.argv[1]; s=open(p).read()
snippet = """
# JSON access log for CrowdSec (2026-10-17)
(accesslog) {
	log {
		output file /var/log/caddy/access.log {
			roll_size 50MiB
			roll_keep 5
		}
		format json
	}
}
"""
i = s.index("}\n", s.index("{\n")) + 2          # after the global options block
s = s[:i] + snippet + s[i:]
# first line after each site block opener "<hosts> {" gets the import
s = re.sub(r"^((?:[a-z0-9.-]+\.example\.com,?\s*\n)*[a-z0-9.-]+\.example\.com \{\n)", r"\1\timport accesslog\n", s, flags=re.M)
open(p, "w").write(s)
P
  caddy validate --config $F --adapter caddyfile >/dev/null 2>&1 && echo caddy-valid
  chown caddy:caddy /var/log/caddy /var/log/caddy/access.log 2>/dev/null || true
  systemctl reload caddy
fi
grep -c "import accesslog" $F
# 2) CrowdSec + nftables bouncer
if ! command -v cscli >/dev/null; then
  curl -fsSL https://install.crowdsec.net | sh >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get -yq install crowdsec crowdsec-firewall-bouncer-nftables >/dev/null
fi
cscli collections install crowdsecurity/caddy crowdsecurity/linux crowdsecurity/sshd crowdsecurity/http-cve crowdsecurity/base-http-scenarios >/dev/null 2>&1 || true
mkdir -p /etc/crowdsec/acquis.d
cat > /etc/crowdsec/acquis.d/caddy.yaml <<'X'
filenames:
  - /var/log/caddy/access.log
labels:
  type: caddy
X
# never ban the overlay or our own hosts
cat > /etc/crowdsec/parsers/s02-enrich/heardos-whitelist.yaml <<'X'
name: heardos/whitelist
description: "HeardOS: never ban the Nebula overlay"
whitelist:
  reason: "HeardOS overlay"
  cidr:
    - 10.42.0.0/24
X
systemctl restart crowdsec; sleep 3
systemctl enable --now crowdsec-firewall-bouncer >/dev/null 2>&1; systemctl restart crowdsec-firewall-bouncer
[ -n "${CS_ENROLL_KEY:-}" ] && cscli console enroll -e context "$CS_ENROLL_KEY" --name heardos-lighthouse && systemctl restart crowdsec
systemctl is-active crowdsec crowdsec-firewall-bouncer | paste -sd' '
cscli bouncers list -o raw | tail -n +2 | cut -d, -f1,3
cscli metrics show acquisition 2>/dev/null | head -12
free -m | sed -n 2p
