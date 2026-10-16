#!/usr/bin/env bash
# 22-vps-lighthouse.sh — run ON THE LAPTOP against any fresh Ubuntu 24.04 VPS (root ssh by key).
# Hardens SSH, sets up ufw + fail2ban + unattended upgrades, installs the Nebula lighthouse bundle, verifies.
# Re-runnable. Caddy (the public edge) is a separate step: 30-vps-caddy.sh.
. "$(dirname "$0")/lib/common.sh"; load_env
: "${NEBULA_LH_IP:?set NEBULA_LH_IP}"
CA="$HOME/.config/nebula-ca"; [ -f "$CA/out/lighthouse/config.yml" ] || die "run 20-nebula-ca.sh init first"

msg "waiting for ssh on root@$NEBULA_LH_IP"
until lh true 2>/dev/null; do sleep 5; done
lh 'command -v cloud-init >/dev/null && cloud-init status --wait >/dev/null 2>&1; true'

msg "base hardening"
lh 'export DEBIAN_FRONTEND=noninteractive
apt-get -qq update && apt-get -qq install -y ufw fail2ban unattended-upgrades curl >/dev/null
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<EOF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
EOF
sshd -t && systemctl reload ssh
printf "[sshd]\nenabled = true\nmaxretry = 4\nbantime = 1h\nfindtime = 10m\n" > /etc/fail2ban/jail.d/sshd.local; systemctl enable --now fail2ban >/dev/null
systemctl disable --now avahi-daemon.socket avahi-daemon.service >/dev/null 2>&1 || true
printf "APT::Periodic::Update-Package-Lists \"1\";\nAPT::Periodic::Unattended-Upgrade \"1\";\n" > /etc/apt/apt.conf.d/20auto-upgrades
printf "net.core.default_qdisc = fq\nnet.ipv4.tcp_congestion_control = bbr\nnet.core.rmem_max = 16777216\nnet.core.wmem_max = 16777216\nnet.core.rmem_default = 1048576\nnet.core.wmem_default = 1048576\nnet.ipv4.tcp_rmem = 4096 131072 16777216\nnet.ipv4.tcp_wmem = 4096 131072 16777216\nnet.ipv4.tcp_mtu_probing = 1\nnet.ipv4.tcp_slow_start_after_idle = 0\nnet.ipv4.tcp_notsent_lowat = 16384\nnet.core.netdev_max_backlog = 5000\n" > /etc/sysctl.d/91-heardos-net.conf; modprobe tcp_bbr 2>/dev/null || true; sysctl -q -p /etc/sysctl.d/91-heardos-net.conf
ufw --force reset >/dev/null; ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null; ufw allow 4242/udp >/dev/null; ufw --force enable >/dev/null
sshd -T | grep -E "^(passwordauthentication|permitrootlogin)"'

msg "nebula lighthouse"
"$HERE/21-nebula-node.sh" lighthouse "root@$NEBULA_LH_IP"

msg "verify: key login ok, password login refused"
lh 'echo key-ok'
ssh -o BatchMode=yes -o PubkeyAuthentication=no -o PreferredAuthentications=password "root@$NEBULA_LH_IP" true 2>&1 | tail -1
msg "pending OS updates: $(lh 'apt list --upgradable 2>/dev/null | grep -c upgradable') — run: ssh root@$NEBULA_LH_IP 'apt-get full-upgrade -y && reboot' when convenient"
msg "next: 21-nebula-node.sh host root@$HOST_LAN_IP ; 21-nebula-node.sh laptop --local ; then 30-vps-caddy.sh after the edge stack is up on the host"
