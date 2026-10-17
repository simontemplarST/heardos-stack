#!/usr/bin/env bash
# Run ON CT 109: serve the HeardOS LAN CA wildcard (*.192-168-10-20.sslip.io) from Traefik and keep it renewed.
set -euo pipefail
T=/root/runtipi/traefik
cat > /usr/local/sbin/heardos-lan-tls.sh <<'X'
#!/bin/sh
# (re)write Traefik's dynamic TLS file; the timestamp changes the content so Traefik reloads the renewed cert
cat > /root/runtipi/traefik/dynamic/tls-lan.yml <<Y
# HeardOS LAN CA wildcard, renewed by step-renew-lan.service - written $(date -u +%FT%TZ)
tls:
  stores:
    default:
      defaultCertificate:
        certFile: /etc/traefik/tls/heardos-lan.crt
        keyFile: /etc/traefik/tls/heardos-lan.key
  certificates:
    - certFile: /etc/traefik/tls/heardos-lan.crt
      keyFile: /etc/traefik/tls/heardos-lan.key
Y
X
chmod 755 /usr/local/sbin/heardos-lan-tls.sh; /usr/local/sbin/heardos-lan-tls.sh
chmod 600 $T/tls/heardos-lan.key
# static config: drop the per-name list (routers override it), quiet logs again
python3 - <<'P'
import re
p="/root/runtipi/traefik/traefik.yml"; s=open(p).read()
s=re.sub(r"        domains:   # routers set tls=true.*?\n(?:          - main: .*\n)+", "", s)
s=s.replace("  level: DEBUG","  level: ERROR")
open(p,"w").write(s)
P
# step CLI for renewals (renew authenticates with the current cert itself; no provisioner secret on this host)
if ! command -v step >/dev/null; then
  curl -fsSL https://packages.smallstep.com/keys/apt/repo-signing-key.gpg -o /etc/apt/trusted.gpg.d/smallstep.asc
  echo "deb [signed-by=/etc/apt/trusted.gpg.d/smallstep.asc] https://packages.smallstep.com/stable/debian debs main" > /etc/apt/sources.list.d/smallstep.list
  apt-get update -q >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get -yq install step-cli >/dev/null
fi
cat > /etc/systemd/system/step-renew-lan.service <<'X'
[Unit]
Description=Renew the HeardOS LAN wildcard cert from step-ca (CT 113) and reload Traefik's TLS file
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/bin/step ca renew --daemon --ca-url https://ca.192-168-10-53.sslip.io --root /root/runtipi/traefik/shared/heardos-lan-root.crt --exec /usr/local/sbin/heardos-lan-tls.sh /root/runtipi/traefik/tls/heardos-lan.crt /root/runtipi/traefik/tls/heardos-lan.key
Restart=always
RestartSec=60
[Install]
WantedBy=multi-user.target
X
systemctl daemon-reload; systemctl enable --now step-renew-lan >/dev/null 2>&1; sleep 2; systemctl is-active step-renew-lan
docker restart traefik >/dev/null; echo traefik-restarted
