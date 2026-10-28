# @id: script_1793120000000_officeegr1
# @title: ONLYOFFICE egress firewall
# @description: Document Server may not open connections to private/internal addresses (WOPI SSRF guard); run by office-egress.service at boot
#!/bin/bash
# office-egress.sh up|down (2026-10-28). ONLYOFFICE Document Server (172.31.251.3 on the office-edge network) serves /hosting/ to
# anyone (the WOPI editor iframe can't do an Authentik hop), and its WOPI client fetches whatever WOPISrc a request names —
# Document Server's own allowPrivateIPAddress filter does NOT apply to WOPI fetches (verified 2026-10-28 with a listener on the
# proxy network). So the kernel refuses every NEW connection from Document Server to private, loopback, link-local, CGNAT or
# multicast addresses — other containers (FORWARD/DOCKER-USER; bridge traffic is filtered since bridge-nf-call-iptables=1) and
# this host itself (INPUT). Public destinations (https://docs.$DOMAIN/wopi via the lighthouse, font/plugin downloads) still work;
# Traefik -> Document Server is inbound, so replies are unaffected. Idempotent.
DS=${OFFICE_DS_IP:-172.31.251.3}
if [ "${1:-up}" = down ]; then
  iptables -D DOCKER-USER -s $DS -j OFFICE-EGRESS 2>/dev/null; iptables -D INPUT -s $DS -j OFFICE-EGRESS 2>/dev/null
  iptables -F OFFICE-EGRESS 2>/dev/null; iptables -X OFFICE-EGRESS 2>/dev/null; exit 0
fi
iptables -N OFFICE-EGRESS 2>/dev/null; iptables -F OFFICE-EGRESS
iptables -A OFFICE-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
for n in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 127.0.0.0/8 169.254.0.0/16 100.64.0.0/10 224.0.0.0/4 0.0.0.0/8; do
  iptables -A OFFICE-EGRESS -d $n -j REJECT --reject-with icmp-admin-prohibited; done
iptables -A OFFICE-EGRESS -j RETURN
iptables -N DOCKER-USER 2>/dev/null
iptables -C DOCKER-USER -s $DS -j OFFICE-EGRESS 2>/dev/null || iptables -I DOCKER-USER 1 -s $DS -j OFFICE-EGRESS
iptables -C INPUT -s $DS -j OFFICE-EGRESS 2>/dev/null || iptables -I INPUT 1 -s $DS -j OFFICE-EGRESS
echo "office-egress: Document Server $DS may reach public addresses only"
