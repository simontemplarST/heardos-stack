#!/usr/bin/env bash
# vps/ssh-overlay-only.sh — runs ON THE VPS as root (piped by 22-vps-lighthouse.sh, over the overlay, once ssh over Nebula
# is proven). Binds sshd to the lighthouse's overlay address only and closes public 22/tcp in ufw. Idempotent.
#   bash -s 10.42.0.1 < ssh-overlay-only.sh      lock ssh to the overlay
#   bash -s --undo    < ssh-overlay-only.sh      give public ssh back (recovery, e.g. from the provider's web console)
# Ubuntu 24.04 runs sshd socket-activated (ssh.socket): a drop-in replaces its ListenStream, with FreeBind so the socket
# binds at boot before nebula1 exists. Hosts running plain ssh.service get ListenAddress + ip_nonlocal_bind instead.
set -euo pipefail
SOCK_DROPIN=/etc/systemd/system/ssh.socket.d/zz-heardos-overlay.conf
SVC_DROPIN=/etc/systemd/system/ssh.service.d/10-heardos-overlay.conf
SSHD_DROPIN=/etc/ssh/sshd_config.d/10-heardos-overlay.conf
SYSCTL=/etc/sysctl.d/94-heardos-ssh-overlay.conf
MARK=/etc/ssh/heardos-overlay-only   # vps/harden.sh leaves public 22 closed while this exists

if systemctl is-enabled -q ssh.socket 2>/dev/null || systemctl is-active -q ssh.socket 2>/dev/null; then mode=socket; else mode=service; fi
restart_ssh() {
  systemctl daemon-reload
  if [ "$mode" = socket ]; then
    # stop the listener first, or the socket refuses to start while ssh.service is active. Established sessions (this one
    # included) are separate processes (KillMode=process) and survive; the next connection re-activates ssh.service.
    systemctl stop ssh.service 2>/dev/null || true; systemctl restart ssh.socket
  else
    systemctl restart ssh.service 2>/dev/null || systemctl restart sshd.service
  fi
}
undo() {
  rm -f "$SOCK_DROPIN" "$SVC_DROPIN" "$SSHD_DROPIN" "$SYSCTL" "$MARK"
  ufw allow 22/tcp >/dev/null
  restart_ssh
}

if [ "${1:-}" = --undo ]; then undo; echo "public ssh restored"; exit 0; fi
IP="${1:?usage: ssh-overlay-only.sh OVERLAY_IP | --undo}"
ip -4 -o addr show dev nebula1 2>/dev/null | grep -q " inet $IP/" || { echo "nebula1 does not carry $IP; ssh left as it is" >&2; exit 1; }

if [ "$mode" = socket ]; then
  install -d /etc/systemd/system/ssh.socket.d
  printf '# HeardOS: ssh only on the Nebula overlay (vps/ssh-overlay-only.sh)\n[Socket]\nListenStream=\nListenStream=%s:22\nFreeBind=true\n' "$IP" > "$SOCK_DROPIN"
else
  printf '# HeardOS: let sshd bind the overlay address before nebula1 is up at boot\nnet.ipv4.ip_nonlocal_bind = 1\n' > "$SYSCTL"
  sysctl -q -p "$SYSCTL"
  printf '# HeardOS: ssh only on the Nebula overlay (vps/ssh-overlay-only.sh)\nListenAddress %s:22\n' "$IP" > "$SSHD_DROPIN"
  install -d /etc/systemd/system/ssh.service.d
  printf '[Unit]\nAfter=nebula.service\nWants=nebula.service\n' > "$SVC_DROPIN"
  sshd -t
fi
restart_ssh; sleep 1

# proven locally before the firewall changes: listening on the overlay address, nowhere else
L="$(ss -Hltn 'sport = :22' | awk '{print $4}' | sort -u | tr '\n' ' ')"
if [ "$L" != "$IP:22 " ]; then
  echo "sshd listens on '$L', not only $IP:22 — rolling back" >&2; undo; exit 1
fi
ufw allow in on nebula1 to any port 22 proto tcp comment 'ssh over the Nebula overlay' >/dev/null
ufw delete allow 22/tcp >/dev/null 2>&1 || true
printf '%s\n' "$IP" > "$MARK"
echo "ssh: $mode bound to $IP:22 only; ufw: 22/tcp only on nebula1"
