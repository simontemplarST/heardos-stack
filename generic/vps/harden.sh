#!/usr/bin/env bash
# vps/harden.sh — runs ON THE VPS as root (piped by 22-vps-lighthouse.sh or the HeardOS installer). Idempotent.
# Key-only SSH, fail2ban, unattended upgrades, network tuning, ufw allowing only ssh + Nebula (Caddy adds 80/443 later).
# ssh is public only until vps/ssh-overlay-only.sh moves it onto the overlay; a re-run never reopens it.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
for i in 1 2 3 4 5 6; do apt-get -qq update && break; sleep 10; done   # first-boot apt locks clear within a minute
apt-get -qq install -y ufw fail2ban unattended-upgrades curl >/dev/null
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<CONF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
CONF
sshd -t && (systemctl reload ssh 2>/dev/null || systemctl reload sshd)
printf "[sshd]\nenabled = true\nmaxretry = 4\nbantime = 1h\nfindtime = 10m\n" > /etc/fail2ban/jail.d/sshd.local; systemctl enable --now fail2ban >/dev/null
systemctl disable --now avahi-daemon.socket avahi-daemon.service >/dev/null 2>&1 || true
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades
cat > /etc/sysctl.d/91-heardos-net.conf <<SYS
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.ipv4.tcp_rmem = 4096 131072 16777216
net.ipv4.tcp_wmem = 4096 131072 16777216
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_notsent_lowat = 16384
net.core.netdev_max_backlog = 5000
SYS
modprobe tcp_bbr 2>/dev/null || true; sysctl -q -p /etc/sysctl.d/91-heardos-net.conf
ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
# public ssh only until 22-vps-lighthouse.sh has proven ssh over the overlay and run vps/ssh-overlay-only.sh (marker file)
if [ -f /etc/ssh/heardos-overlay-only ]; then ufw allow in on nebula1 to any port 22 proto tcp >/dev/null; else ufw allow 22/tcp >/dev/null; fi
ufw allow 4242/udp >/dev/null; ufw --force enable >/dev/null
sshd -T | grep -E "^(passwordauthentication|permitrootlogin)"
echo "HEARDOS_OUTPUT VPS_UPGRADABLE=$(apt list --upgradable 2>/dev/null | grep -c upgradable || true)"
