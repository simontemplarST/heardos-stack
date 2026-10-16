# Home LAN over Nebula

Members of the Nebula `admins` group can reach the home LAN (192.168.10.0/24) from anywhere, routed through the Proxmox
node. Nothing is exposed on the home router.

## Router side: `nebula-lan/prox-enable-lan-route.sh`

- Re-signs the Proxmox node's certificate with `-subnets 192.168.10.0/24` (Nebula refuses an unsafe route unless the
  `via` host's certificate carries the subnet). The CA lives on the laptop in `~/.config/nebula-ca`.
- Adds a firewall rule `{port: any, proto: any, group: admins, local_cidr: 192.168.10.0/24}`. Since Nebula 1.9 inbound rules
  only match the node's own overlay address unless `local_cidr` is set; without it the packets are dropped before the
  tun interface and nothing is logged.
- Enables `net.ipv4.ip_forward` and installs `nebula-lan-nat.service`, which masquerades 10.42.0.0/24 onto the LAN
  interface and accepts forwarding between `nebula1` and the bridge, so LAN devices need no return route.
- Restarts Nebula through a detached `systemd-run`, because running it over the overlay drops the SSH session that
  started it. `--print` emits a paste-able version for the Proxmox web shell when SSH is unreachable.

## Client side: `nebula-lan/laptop-add-lan-route.sh`

Adds `tun.unsafe_routes: [{route: 192.168.10.0/24, via: 10.42.0.10, mtu: 1300, metric: 1000}]` to the laptop's Nebula
config and restarts it. Metric 1000 keeps the home Wi-Fi's own connected route (metric 600) preferred when the laptop
is at home, so LAN traffic only crosses the overlay when away. `--undo` removes it.

## Verifying

`ping 192.168.10.2` and `ssh root@192.168.10.2` from outside the house. The target sees the client as the Proxmox node's LAN
address because of the masquerade. Expect roughly 2× the overlay round trip.
