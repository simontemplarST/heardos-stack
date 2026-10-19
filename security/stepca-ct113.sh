#!/usr/bin/env bash
# Run ON prox as root with SSH_PUBKEY set: CT 113 "ca" (Debian 12) running step-ca with an ACME provisioner.
set -euo pipefail
ID=113
pct status $ID >/dev/null 2>&1 || pct create $ID Media:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst \
  --hostname ca --unprivileged 1 --cores 1 --memory 512 --swap 256 --rootfs Bittytb:4 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp,type=veth --onboot 1 --ssh-public-keys <(echo "$SSH_PUBKEY") \
  --description "step-ca private ACME CA for HeardOS LAN names (2026-10-17)" --features nesting=0
pct status $ID | grep -q running || pct start $ID
for i in $(seq 30); do IP=$(pct exec $ID -- sh -c "ip -4 -o addr show eth0 | awk '{print \$4}' | cut -d/ -f1" 2>/dev/null); [ -n "$IP" ] && break; sleep 2; done
echo "CT $ID IP=$IP"
pct exec $ID -- bash -c '
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -q >/dev/null && apt-get -yq dist-upgrade >/dev/null && apt-get -yq install curl gpg ca-certificates unattended-upgrades >/dev/null
if ! command -v step-ca >/dev/null; then
  # 2026-10-19: key pinned by fingerprint and kept out of trusted.gpg.d (that would trust it for every repo)
  install -d -m 755 /etc/apt/keyrings; rm -f /etc/apt/trusted.gpg.d/smallstep.asc
  curl -fsSL https://packages.smallstep.com/keys/apt/repo-signing-key.gpg -o /etc/apt/keyrings/smallstep.asc
  gpg --show-keys --with-colons /etc/apt/keyrings/smallstep.asc 2>/dev/null | grep "^fpr:" | cut -d: -f10 | grep -qx 35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3 \
    || { echo "unexpected smallstep apt signing key; refusing it" >&2; rm -f /etc/apt/keyrings/smallstep.asc; exit 1; }
  echo "deb [signed-by=/etc/apt/keyrings/smallstep.asc] https://packages.smallstep.com/stable/debian debs main" > /etc/apt/sources.list.d/smallstep.list
  apt-get update -q >/dev/null && apt-get -yq install step-cli step-ca >/dev/null
fi
step version | head -1; step-ca version | head -1
printf "PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n" > /etc/ssh/sshd_config.d/00-heardos-baseline.conf; systemctl reload ssh || true
'
