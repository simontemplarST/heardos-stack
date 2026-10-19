#!/usr/bin/env bash
# 10-host-bootstrap.sh — run ON THE COMPUTE HOST as root, once. Idempotent.
# Installs Docker Engine + compose plugin, Portainer CE, creates the HeardOS layout and the shared
# "proxy" network, hardens SSH (stops if no authorized key exists, unless FORCE=1), configures ufw and unattended upgrades.
. "$(dirname "$0")/lib/common.sh"; require_root; load_env

export DEBIAN_FRONTEND=noninteractive
msg "apt packages"
apt-get -qq update
apt-get -qq install -y ca-certificates curl gnupg ufw unattended-upgrades fail2ban gettext-base jq python3 sqlite3 >/dev/null

if ! command -v docker >/dev/null; then
  msg "Docker Engine (official repo)"
  install -m 0755 -d /etc/apt/keyrings
  k="$(mktemp)"; curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o "$k"
  # Docker's release key, pinned by fingerprint (docs.docker.com/engine/install/ubuntu)
  gpg --show-keys --with-colons "$k" 2>/dev/null | awk -F: '$1=="fpr"{print $10}' | grep -qx 9DC858229FC7DD38854AE2D88D81803C0EBFCD88 \
    || { rm -f "$k"; die "the key at download.docker.com is not Docker's release key; refusing it"; }
  gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg < "$k"; chmod 644 /etc/apt/keyrings/docker.gpg; rm -f "$k"
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get -qq update && apt-get -qq install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
fi
systemctl enable --now docker >/dev/null
docker --version; docker compose version

msg "layout under $HEARDOS_ROOT"
install -d -m 755 "$HEARDOS_ROOT"/{stacks,data,dynamic,media/music,media/photos,media/documents}
install -d -m 700 "$HEARDOS_ROOT/secrets"
# "proxy" needs a user-defined subnet: Traefik gets a fixed address on it (TRAEFIK_PROXY_IP, outside the dynamic
# --ip-range) and every app that trusts identity / forwarded headers trusts that /32 only, not the whole network.
: "${PROXY_SUBNET:=172.30.0.0/24}" "${PROXY_IP_RANGE:=172.30.0.128/25}" "${TRAEFIK_PROXY_IP:=172.30.0.2}"
if ! docker network inspect proxy >/dev/null 2>&1; then
  docker network create --subnet "$PROXY_SUBNET" --ip-range "$PROXY_IP_RANGE" proxy >/dev/null
elif [ -z "$(docker network inspect proxy -f '{{range .IPAM.Config}}{{.IPRange}}{{end}}')" ]; then
  warn "the existing proxy network has no fixed ip-range: Traefik's static address may collide. Stop all stacks, then:"
  warn "  docker network rm proxy && docker network create --subnet $PROXY_SUBNET --ip-range $PROXY_IP_RANGE proxy"
fi
PROXY_SUBNET="$(docker network inspect proxy -f '{{(index .IPAM.Config 0).Subnet}}')"
for kv in "PROXY_SUBNET=$PROXY_SUBNET" "PROXY_IP_RANGE=$PROXY_IP_RANGE" "TRAEFIK_PROXY_IP=$TRAEFIK_PROXY_IP"; do
  grep -q "^${kv%%=*}=" "$ENV_FILE" || echo "$kv" >> "$ENV_FILE"
done
msg "proxy network: $PROXY_SUBNET (Traefik $TRAEFIK_PROXY_IP)"; out PROXY_SUBNET "$PROXY_SUBNET"; out TRAEFIK_PROXY_IP "$TRAEFIK_PROXY_IP"
# Traefik publishes :80 on the LAN and overlay addresses only; let it bind the overlay address at boot before nebula1 is up
printf 'net.ipv4.ip_nonlocal_bind = 1\n' > /etc/sysctl.d/60-heardos-nonlocal-bind.conf && sysctl -q -p /etc/sysctl.d/60-heardos-nonlocal-bind.conf

# Portainer starts with its admin ("admin") already set from a generated secret, so nobody on the LAN can claim the
# first-admin screen before us. The file is root-only and mounted read-only; an existing file (e.g. pre-seeded by the
# installer) is kept.
PW_FILE="$HEARDOS_ROOT/secrets/portainer-admin-password"
[ -s "$PW_FILE" ] || ( umask 077; gen_secret 32 > "$PW_FILE" ); chmod 600 "$PW_FILE"
if ! docker ps -a --format '{{.Names}}' | grep -qx portainer; then
  msg "Portainer CE on https://$HOST_LAN_IP:9443 (user admin, password in $PW_FILE)"
  docker volume create portainer_data >/dev/null
  docker run -d --name portainer --restart=always -p "$HOST_LAN_IP:9443:9443" \
    -v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data \
    -v "$PW_FILE:/run/secrets/portainer-admin-password:ro" \
    portainer/portainer-ce:lts --admin-password-file /run/secrets/portainer-admin-password >/dev/null
  out PORTAINER_FRESH 1
fi
out PORTAINER_ADMIN_PASSWORD "$(cat "$PW_FILE")"   # the installer moves this into its vault

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
elif [ -n "${FORCE:-}" ]; then
  warn "no authorized_keys found — FORCE set: leaving password SSH on; add a key and re-run without FORCE"
else
  die "no authorized_keys for root or any user: refusing to continue with password SSH on. Add your key (ssh-copy-id) and re-run, or set FORCE=1 to skip SSH hardening"
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
