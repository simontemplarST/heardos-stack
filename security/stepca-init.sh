#!/usr/bin/env bash
# Run INSIDE CT 113 as root: initialise step-ca (ACME, 90-day certs), systemd service. Idempotent-ish: skips init if ca.json exists.
set -euo pipefail
export STEPPATH=/etc/step-ca
id step >/dev/null 2>&1 || useradd --system --home /etc/step-ca --shell /bin/false step
install -d -m 700 -o step -g step $STEPPATH
if [ ! -f $STEPPATH/config/ca.json ]; then
  umask 077
  head -c 32 /dev/urandom | base64 > $STEPPATH/password.txt
  head -c 32 /dev/urandom | base64 > /root/provisioner-password.txt
  step ca init --deployment-type=standalone --name="HeardOS LAN CA" \
    --dns="ca.192-168-10-53.sslip.io,192.168.10.53" --address=":443" \
    --provisioner="admin" --password-file=$STEPPATH/password.txt \
    --provisioner-password-file=/root/provisioner-password.txt --acme >/dev/null
  # 90-day certs for ACME clients (Traefik renews at 30 days left)
  python3 - <<'P'
import json
p="/etc/step-ca/config/ca.json"; c=json.load(open(p))
for pr in c["authority"]["provisioners"]:
    if pr["type"]=="ACME":
        pr["claims"]={"minTLSCertDuration":"5m","maxTLSCertDuration":"2160h","defaultTLSCertDuration":"2160h"}
open(p,"w").write(json.dumps(c, indent=2))
P
  chown -R step:step $STEPPATH
fi
cat > /etc/systemd/system/step-ca.service <<'X'
[Unit]
Description=step-ca (HeardOS LAN CA)
After=network-online.target
Wants=network-online.target
[Service]
User=step
Group=step
Environment=STEPPATH=/etc/step-ca
WorkingDirectory=/etc/step-ca
ExecStart=/usr/bin/step-ca /etc/step-ca/config/ca.json --password-file /etc/step-ca/password.txt
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=full
ReadWritePaths=/etc/step-ca/db
ProtectHome=yes
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
X
systemctl daemon-reload; systemctl enable --now step-ca >/dev/null 2>&1; systemctl restart step-ca; sleep 3
systemctl is-active step-ca
curl -sk https://127.0.0.1/health; echo
curl -sk https://127.0.0.1/acme/acme/directory | head -c 200; echo
step certificate fingerprint $STEPPATH/certs/root_ca.crt
