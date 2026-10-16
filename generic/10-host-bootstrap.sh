#!/usr/bin/env bash
# 10-host-bootstrap.sh — run ON THE COMPUTE HOST as root, once. Idempotent.
# Installs Docker Engine + compose plugin, Portainer CE, creates the HeardOS layout and the shared
# "proxy" network, hardens SSH (only if an authorized key exists), configures ufw and unattended upgrades.
. "$(dirname "$0")/lib/common.sh"; require_root; load_env

export DEBIAN_FRONTEND=noninteractive
msg "apt packages"
apt-get -qq update
apt-get -qq install -y ca-certificates curl gnupg ufw unattended-upgrades fail2ban gettext-base jq python3 sqlite3 >/dev/null

if ! command -v docker >/dev/null; then
  msg "Docker Engine (official repo)"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get -qq update && apt-get -qq install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
fi
systemctl enable --now docker >/dev/null
docker --version; docker compose version

msg "layout under $HEARDOS_ROOT"
install -d -m 755 "$HEARDOS_ROOT"/{stacks,data,dynamic,media/music,media/photos,media/documents}
install -d -m 700 "$HEARDOS_ROOT/secrets"
docker network inspect proxy >/dev/null 2>&1 || docker network create proxy >/dev/null
PROXY_SUBNET="$(docker network inspect proxy -f '{{(index .IPAM.Config 0).Subnet}}')"
grep -q '^PROXY_SUBNET=' "$ENV_FILE" || echo "PROXY_SUBNET=$PROXY_SUBNET" >> "$ENV_FILE"
msg "proxy network: $PROXY_SUBNET"; out PROXY_SUBNET "$PROXY_SUBNET"

if ! docker ps -a --format '{{.Names}}' | grep -qx portainer; then
  msg "Portainer CE on https://$HOST_LAN_IP:9443 (set the admin password in the UI within 5 minutes of first start)"
  docker volume create portainer_data >/dev/null
  docker run -d --name portainer --restart=always -p "$HOST_LAN_IP:9443:9443" \
    -v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data portainer/portainer-ce:lts >/dev/null
  out PORTAINER_FRESH 1   # the installer sets the admin password via the API inside Portainer's 5-minute window
fi

msg "kernel: TUN + socket buffers for Nebula"
[ -e /dev/net/tun ] || die "/dev/net/tun missing — load the tun module (modprobe tun) or use a kernel that has it"
cat > /etc/sysctl.d/91-heardos-net.conf <<'SYS'
# network tuning: BBR + fq, bigger buffers, MTU probing for the Nebula tunnel path
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

if [ -s /root/.ssh/authorized_keys ] || compgen -G "/home/*/.ssh/authorized_keys" >/dev/null; then
  msg "sshd: key-only"
  cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
X11Forwarding no
MaxAuthTries 3
EOF
  sshd -t && systemctl reload ssh
else
  warn "no authorized_keys found — leaving password SSH on; add a key and re-run"
fi
printf '[sshd]\nenabled = true\nmaxretry = 4\nbantime = 1h\n' > /etc/fail2ban/jail.d/sshd.local; systemctl enable --now fail2ban >/dev/null

msg "ufw: ssh + http from LAN and overlay, Portainer from LAN, Nebula outbound only (adds rules; never resets your own)"
ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
for src in "$LAN_SUBNET" "$OVERLAY_CIDR"; do ufw allow from "$src" to any port 22 proto tcp >/dev/null; ufw allow from "$src" to any port 80 proto tcp >/dev/null; done
ufw allow from "$LAN_SUBNET" to any port 9443 proto tcp >/dev/null
ufw --force enable >/dev/null; ufw status | sed -n '1,12p'
# docker publishes ports with its own iptables rules that bypass ufw; stacks below bind LAN ports to HOST_LAN_IP explicitly.

printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades
systemctl enable --now unattended-upgrades >/dev/null
msg "done. next: 20-nebula-ca.sh on the laptop, then 22-vps-lighthouse.sh, then 21-nebula-node.sh here."
