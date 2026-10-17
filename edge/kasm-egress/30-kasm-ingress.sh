#!/bin/sh
# Kasm VM 110 inbound filter (2026-10-17). Install as /usr/local/sbin/kasm-ingress.sh; `up` / `down`. Idempotent.
# On eth0 (LAN) only these may start a connection:
#   - tcp/443 to the Kasm proxy from CT 109 Traefik (192.168.10.20) -> browser.example.com keeps working
#   - udp/4242 nebula underlay, DHCP, ICMP / ICMPv6
# Everything else from the LAN is dropped: sshd 22, Kasm RDP gateway 3389, LLMNR 5355, 443 from other hosts.
# SSH stays available over the overlay (heardos@10.42.0.12, nebula group admins). nebula1/docker bridges are untouched.
TRAEFIK=192.168.10.20
if [ "$1" = down ]; then
  iptables -D INPUT -j KASM-INGRESS 2>/dev/null; iptables -D DOCKER-USER -j KASM-INGRESS-FWD 2>/dev/null
  ip6tables -D INPUT -j KASM-INGRESS 2>/dev/null; ip6tables -D DOCKER-USER -j KASM-INGRESS-FWD 2>/dev/null
  for t in iptables ip6tables; do for c in KASM-INGRESS KASM-INGRESS-FWD; do $t -F $c 2>/dev/null; $t -X $c 2>/dev/null; done; done
  exit 0
fi
# --- IPv4: host services (INPUT)
iptables -N KASM-INGRESS 2>/dev/null; iptables -F KASM-INGRESS
iptables -A KASM-INGRESS ! -i eth0 -j RETURN
iptables -A KASM-INGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
iptables -A KASM-INGRESS -p icmp -j RETURN
iptables -A KASM-INGRESS -p udp --dport 68 -j RETURN
iptables -A KASM-INGRESS -p udp --dport 4242 -j RETURN
iptables -A KASM-INGRESS -j DROP
iptables -C INPUT -j KASM-INGRESS 2>/dev/null || iptables -I INPUT 1 -j KASM-INGRESS
# --- IPv4: docker-published ports (FORWARD via DOCKER-USER; match the original destination port before DNAT)
iptables -N DOCKER-USER 2>/dev/null
iptables -N KASM-INGRESS-FWD 2>/dev/null; iptables -F KASM-INGRESS-FWD
iptables -A KASM-INGRESS-FWD ! -i eth0 -j RETURN
iptables -A KASM-INGRESS-FWD -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
iptables -A KASM-INGRESS-FWD -s $TRAEFIK -p tcp -m conntrack --ctorigdstport 443 -j RETURN
iptables -A KASM-INGRESS-FWD -j DROP
iptables -C DOCKER-USER -j KASM-INGRESS-FWD 2>/dev/null || iptables -I DOCKER-USER 1 -j KASM-INGRESS-FWD
# --- IPv6: nothing from the LAN except ND/ICMPv6 and DHCPv6
ip6tables -N KASM-INGRESS 2>/dev/null; ip6tables -F KASM-INGRESS
ip6tables -A KASM-INGRESS ! -i eth0 -j RETURN
ip6tables -A KASM-INGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
ip6tables -A KASM-INGRESS -p ipv6-icmp -j RETURN
ip6tables -A KASM-INGRESS -p udp --dport 546 -j RETURN
ip6tables -A KASM-INGRESS -j DROP
ip6tables -C INPUT -j KASM-INGRESS 2>/dev/null || ip6tables -I INPUT 1 -j KASM-INGRESS
ip6tables -N DOCKER-USER 2>/dev/null
ip6tables -N KASM-INGRESS-FWD 2>/dev/null; ip6tables -F KASM-INGRESS-FWD
ip6tables -A KASM-INGRESS-FWD ! -i eth0 -j RETURN
ip6tables -A KASM-INGRESS-FWD -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
ip6tables -A KASM-INGRESS-FWD -j DROP
ip6tables -C DOCKER-USER -j KASM-INGRESS-FWD 2>/dev/null || ip6tables -I DOCKER-USER 1 -j KASM-INGRESS-FWD
exit 0
