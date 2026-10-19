#!/usr/bin/env bash
# crowdsec-install.sh — run ON THE VPS as root (30-vps-caddy.sh must have run: Caddy writes /var/log/caddy/access.log).
# CrowdSec agent + nftables firewall bouncer; Caddy access log, sshd and system logs as sources; the Nebula overlay is
# never banned. Env: OVERLAY_CIDR (default 10.42.0.0/24), optional CS_ENROLL_KEY (free console at app.crowdsec.net),
# optional CS_NAME (engine name shown in the console). Idempotent.
set -euo pipefail
OVERLAY_CIDR="${OVERLAY_CIDR:-10.42.0.0/24}"
export DEBIAN_FRONTEND=noninteractive
# apt_key URL FINGERPRINT KEYRING — install a repo signing key only if it carries the expected primary fingerprint
apt_key() {
  local t; t="$(mktemp)"; curl -fsSL "$1" -o "$t"
  gpg --show-keys --with-colons "$t" 2>/dev/null | awk -F: '$1=="fpr"{print $10}' | grep -qx "$2" \
    || { echo "signing key from $1 is not $2; refusing it" >&2; rm -f "$t"; exit 1; }
  gpg --dearmor --yes -o "$3" < "$t"; chmod 644 "$3"; rm -f "$t"
}
if ! command -v cscli >/dev/null; then
  # CrowdSec's packagecloud apt repo directly (what install.crowdsec.net sets up), key pinned by fingerprint
  apt-get -qq install -y curl gnupg ca-certificates >/dev/null
  install -d -m 755 /etc/apt/keyrings
  apt_key https://packagecloud.io/crowdsec/crowdsec/gpgkey 6A89E3C2303A901A889971D3376ED5326E93CD0C /etc/apt/keyrings/crowdsec_crowdsec-archive-keyring.gpg
  echo "deb [signed-by=/etc/apt/keyrings/crowdsec_crowdsec-archive-keyring.gpg] https://packagecloud.io/crowdsec/crowdsec/any/ any main" > /etc/apt/sources.list.d/crowdsec_crowdsec.list
  apt-get -qq update >/dev/null
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
# Actual Budget's web app fetches its own SQL migrations (/data/migrations/*.sql) on every load; the generic
# http-sensitive-files scenario bans whoever opens Budget for that (it banned the live home IP, 2026-10-18).
cat > /etc/crowdsec/parsers/s02-enrich/heardos-app-paths-whitelist.yaml <<X
name: heardos/app-paths-whitelist
description: "HeardOS: app traffic that looks like probing but isn't"
whitelist:
  reason: "HeardOS app traffic"
  expression:
    - "evt.Meta.target_fqdn == 'budget.${DOMAIN}' && evt.Meta.http_path startsWith '/data/'"
X
# optional: the family's own browsing must not ban the home connection (the home server's Nebula link comes from it:
# a ban there 502s every site). Exempted only from the http behaviour scenarios that normal app use trips (crawling,
# probing 404s, sensitive-looking paths, odd user agents, admin pages); sshd, CVE exploits, brute force, sqli/xss stay
# bannable, so a compromised device at home is still stopped. Done after the overflow, where the scenario is known.
rm -f /etc/crowdsec/parsers/s02-enrich/heardos-home-whitelist.yaml /etc/crowdsec/postoverflows/s01-whitelist/heardos-home-whitelist.yaml   # older kits exempted the IP from everything
if [ -n "${HOME_PUBLIC_IP:-}" ]; then
[[ "$HOME_PUBLIC_IP" =~ ^[0-9A-Fa-f.:]+$ ]] || { echo "HOME_PUBLIC_IP '$HOME_PUBLIC_IP' is not an IP address" >&2; exit 1; }
install -d /etc/crowdsec/postoverflows/s01-whitelist
cat > /etc/crowdsec/postoverflows/s01-whitelist/heardos-home-whitelist.yaml <<X
name: heardos/home-whitelist
description: "HeardOS: the home connection is not banned for the family's own browsing (http behaviour scenarios only)"
whitelist:
  reason: "HeardOS home connection"
  expression:
    - "evt.Overflow.Alert.Source.IP == '${HOME_PUBLIC_IP}' && evt.Overflow.Alert.GetScenario() in ['crowdsecurity/http-crawl-non_statics', 'crowdsecurity/http-probing', 'crowdsecurity/http-sensitive-files', 'crowdsecurity/http-bad-user-agent', 'crowdsecurity/http-admin-interface-probing']"
X
fi
systemctl restart crowdsec; sleep 3
# the package's automatic bouncer registration can leave a key the local API rejects ("access forbidden"): (re)register ours
C=/etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml
if ! systemctl restart crowdsec-firewall-bouncer 2>/dev/null || ! sleep 3 || ! systemctl is-active -q crowdsec-firewall-bouncer; then
  cscli bouncers delete heardos-nftables >/dev/null 2>&1 || true
  K=$(cscli bouncers add heardos-nftables -o raw)
  # rewrite the key with shell builtins only (a sed expression would put it on a command line)
  ( umask 077; { grep -v '^api_key:' "$C"; printf 'api_key: %s\n' "$K"; } > "$C.new" ) && mv "$C.new" "$C"; chmod 600 "$C"; unset K
  systemctl restart crowdsec-firewall-bouncer; sleep 3
fi
systemctl enable -q crowdsec crowdsec-firewall-bouncer
if [ -n "${CS_ENROLL_KEY:-}" ]; then
  # cscli only takes the key as an argument: it is visible to root on this VPS for the second the call runs
  cscli console enroll -e context "$CS_ENROLL_KEY" --name "${CS_NAME:-heardos-edge}" >/dev/null && systemctl restart crowdsec
  echo "enrolled: accept the engine at https://app.crowdsec.net (Security Engines)"
fi
systemctl is-active crowdsec crowdsec-firewall-bouncer | paste -sd' '
