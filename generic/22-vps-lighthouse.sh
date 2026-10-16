#!/usr/bin/env bash
# 22-vps-lighthouse.sh — run ON THE LAPTOP against any fresh Ubuntu 24.04 VPS (root ssh by key).
# Hardens SSH, sets up ufw + fail2ban + unattended upgrades, installs the Nebula lighthouse bundle, verifies.
# Re-runnable. Caddy (the public edge) is a separate step: 30-vps-caddy.sh.
. "$(dirname "$0")/lib/common.sh"; load_env
: "${NEBULA_LH_IP:?set NEBULA_LH_IP}"
CA="$HOME/.config/nebula-ca"; [ -f "$CA/out/lighthouse/config.yml" ] || die "run 20-nebula-ca.sh init first"

msg "waiting for ssh on root@$NEBULA_LH_IP (up to 10 minutes)"
wait_ssh 600 || die "the VPS did not accept ssh within 10 minutes; check the IP and that your key is on root"
for i in 1 2 3 4 5; do lh 'command -v cloud-init >/dev/null && cloud-init status --wait >/dev/null 2>&1; true' && break; sleep 10; done

msg "base hardening (vps/harden.sh)"
lh 'bash -s' < "$HERE/vps/harden.sh"

msg "nebula lighthouse"
"$HERE/21-nebula-node.sh" lighthouse "root@$NEBULA_LH_IP"

msg "verify: key login ok, password login refused"
lh 'echo key-ok'
ssh -o BatchMode=yes -o PubkeyAuthentication=no -o PreferredAuthentications=password "root@$NEBULA_LH_IP" true 2>&1 | tail -1
msg "pending OS updates: $(lh 'apt list --upgradable 2>/dev/null | grep -c upgradable') — run: ssh root@$NEBULA_LH_IP 'apt-get full-upgrade -y && reboot' when convenient"
msg "next: 21-nebula-node.sh host root@$HOST_LAN_IP ; 21-nebula-node.sh laptop --local ; then 30-vps-caddy.sh after the edge stack is up on the host"
