#!/usr/bin/env bash
# 22-vps-lighthouse.sh — run ON THE LAPTOP against any fresh Ubuntu 24.04 VPS (root ssh by key).
# Hardens SSH, sets up ufw + fail2ban + unattended upgrades, installs the Nebula lighthouse bundle, verifies, and — once
# this laptop reaches the VPS over the overlay — moves ssh onto the overlay only (public 22 closed).
#   22-vps-lighthouse.sh              everything (public ssh stays open if this laptop is not enrolled yet)
#   22-vps-lighthouse.sh --lock-ssh   only the last part: run it after 21-nebula-node.sh laptop --local
# Re-runnable. Caddy (the public edge) is a separate step: 30-vps-caddy.sh.
. "$(dirname "$0")/lib/common.sh"; load_env
: "${NEBULA_LH_IP:?set NEBULA_LH_IP}" "${NEBULA_LH_OVERLAY:?set NEBULA_LH_OVERLAY}"
CA="$HOME/.config/nebula-ca"; [ -f "$CA/out/lighthouse/config.yml" ] || die "run 20-nebula-ca.sh init first"
ovl() { NEBULA_LH_SSH="$NEBULA_LH_OVERLAY" lh "$@"; }   # ssh to the VPS over the overlay

lock_ssh() {
  msg "ssh over the overlay ($NEBULA_LH_OVERLAY)"
  if ! { ping -c 2 -W 3 "$NEBULA_LH_OVERLAY" >/dev/null 2>&1 && ovl true 2>/dev/null; }; then
    warn "this laptop does not reach the VPS over the overlay yet: public ssh stays open."
    warn "after 21-nebula-node.sh laptop --local, run: $0 --lock-ssh"
    return 0
  fi
  # run over the overlay session itself, so the proof and the change use the same path
  ovl "bash -s '$NEBULA_LH_OVERLAY'" < "$HERE/vps/ssh-overlay-only.sh" || die "binding ssh to the overlay failed (public ssh left open)"
  if ovl true; then
    set_plan NEBULA_LH_SSH "$NEBULA_LH_OVERLAY"; export NEBULA_LH_SSH="$NEBULA_LH_OVERLAY"
    msg "ssh to the VPS is overlay-only now (root@$NEBULA_LH_OVERLAY); NEBULA_LH_SSH recorded in the plan file"
  else
    die "a fresh ssh over the overlay failed after the change. Recover from the provider's web console: bash /dev/stdin --undo < vps/ssh-overlay-only.sh (or delete /etc/systemd/system/ssh.socket.d/zz-heardos-overlay.conf, systemctl daemon-reload, systemctl restart ssh.socket, ufw allow 22/tcp)"
  fi
}

if [ "${1:-}" = --lock-ssh ]; then lock_ssh; exit 0; fi

msg "waiting for ssh on root@$(_lhaddr) (up to 10 minutes)"
wait_ssh 600 || die "the VPS did not accept ssh within 10 minutes; check the IP and that your key is on root"
for i in 1 2 3 4 5; do lh 'command -v cloud-init >/dev/null && cloud-init status --wait >/dev/null 2>&1; true' && break; sleep 10; done

msg "base hardening (vps/harden.sh)"
lh 'bash -s' < "$HERE/vps/harden.sh"

msg "nebula lighthouse"
"$HERE/21-nebula-node.sh" lighthouse "root@$(_lhaddr)"

msg "verify: key login ok, password login refused"
lh 'echo key-ok'
ssh -o BatchMode=yes -o PubkeyAuthentication=no -o PreferredAuthentications=password "root@$(_lhaddr)" true 2>&1 | tail -1

lock_ssh
msg "pending OS updates: $(lh 'apt list --upgradable 2>/dev/null | grep -c upgradable') — run: ssh root@$(_lhaddr) 'apt-get full-upgrade -y && reboot' when convenient"
msg "next: 21-nebula-node.sh host root@$HOST_LAN_IP ; 21-nebula-node.sh laptop --local (then $0 --lock-ssh if ssh is still public) ; then 30-vps-caddy.sh after the edge stack is up on the host"
