#!/usr/bin/env bash
# vps/wg-egress.sh — run ON THE VPS as root (piped by the laptop):  bash -s NAME SUBNET_PREFIX LISTEN_PORT [TCP_PORT...]
# Makes the VPS a WireGuard exit for ONE host-side container (a gluetun "custom" client): interface wg-NAME holds
# SUBNET_PREFIX.1/30, the peer is SUBNET_PREFIX.2, the peer's traffic leaves eth-out masqueraded as the VPS address, and each
# TCP_PORT arriving on the public address is forwarded to the peer (inbound peer ports, e.g. Soulseek's listen port).
# Idempotent: keys are made once (/etc/wireguard/NAME.{key,peer.key}); prints PUBKEY=, PEER_PRIVATE_KEY=, PEER_ADDRESS=.
# Undo: systemctl disable --now wg-quick@wg-NAME; rm /etc/wireguard/wg-NAME.conf; ufw delete the rules tagged "wg-NAME".
set -euo pipefail
name="${1:?name}"; pfx="${2:?subnet prefix, e.g. 10.77.0}"; port="${3:?listen port}"; shift 3; fwd="$*"
ifc="wg-$name"; peer="$pfx.2"; out="$(ip -4 route show default | awk '{print $5; exit}')"
command -v wg >/dev/null || { DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard-tools >/dev/null; }
umask 077; cd /etc/wireguard
[ -f "$name.key" ] || wg genkey > "$name.key"
[ -f "$name.peer.key" ] || wg genkey > "$name.peer.key"
up=""; down=""
add() { up="${up}PostUp = iptables $1"$'\n'; down="${down}PostDown = iptables ${1/ -A / -D }"$'\n'; }
add "-t nat -A POSTROUTING -s $peer/32 -o $out -j MASQUERADE"
add "-t mangle -A FORWARD -o $ifc -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu"
add "-t mangle -A FORWARD -i $ifc -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu"
for p in $fwd; do add "-t nat -A PREROUTING -i $out -p tcp --dport $p -j DNAT --to-destination $peer:$p"; done
cat > "$ifc.conf" <<EOF
# $ifc: WireGuard exit for one container (heardos vps/wg-egress.sh). Peer $peer leaves via $out; forwarded tcp: ${fwd:-none}
[Interface]
Address = $pfx.1/30
ListenPort = $port
MTU = 1380
PrivateKey = $(cat "$name.key")
${up}${down}
[Peer]
PublicKey = $(wg pubkey < "$name.peer.key")
AllowedIPs = $peer/32
EOF
sysctl -q -w net.ipv4.ip_forward=1; echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/94-wg-egress.conf
ufw allow "$port/udp" comment "$ifc" >/dev/null
ufw route allow in on "$ifc" out on "$out" from "$peer" comment "$ifc egress" >/dev/null
for p in $fwd; do ufw route allow in on "$out" out on "$ifc" to "$peer" port "$p" proto tcp comment "$ifc inbound $p" >/dev/null; done
systemctl enable -q "wg-quick@$ifc"; systemctl restart "wg-quick@$ifc"
echo "PUBKEY=$(wg pubkey < "$name.key")"
echo "PEER_PRIVATE_KEY=$(cat "$name.peer.key")"
echo "PEER_ADDRESS=$peer/32"
