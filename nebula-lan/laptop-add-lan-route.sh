#!/usr/bin/env bash
# laptop-add-lan-route.sh — teach this laptop's Nebula node to reach the house LAN 192.168.10.0/24 through prox.
#   sudo bash laptop-add-lan-route.sh          add the unsafe_route and restart nebula
#   sudo bash laptop-add-lan-route.sh --undo   remove it again
# Run prox-enable-lan-route.sh FIRST (prox's cert must carry the subnet, or nebula refuses the route).
# metric 1000 keeps the overlay route BELOW the Wi-Fi's own connected route (metric 600) when you are at home,
# so LAN traffic only goes through Region-1 when you are away.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
CFG=/etc/nebula/config.yml
cp -p "$CFG" "$CFG.bak-$(date +%Y%m%d-%H%M%S)"
python3 - "$CFG" "${1:-}" <<'EOF'
import sys, yaml
cfg, mode = sys.argv[1], sys.argv[2]
d = yaml.safe_load(open(cfg))
tun = d.setdefault("tun", {})
routes = [r for r in (tun.get("unsafe_routes") or []) if r.get("route") != "192.168.10.0/24"]
if mode != "--undo":
    routes.append({"route": "192.168.10.0/24", "via": "10.42.0.10", "mtu": 1300, "metric": 1000})
if routes: tun["unsafe_routes"] = routes
else: tun.pop("unsafe_routes", None)
yaml.safe_dump(d, open(cfg, "w"), sort_keys=False, default_flow_style=False)
print("unsafe_routes:", tun.get("unsafe_routes"))
EOF
/usr/local/bin/nebula -config "$CFG" -test && echo "config ok"
systemctl restart nebula; sleep 3
systemctl is-active nebula
ip route show 192.168.10.0/24
ping -c 2 -W 3 192.168.10.2 && echo "LAN reachable via the overlay (or directly, if you are at home)"
