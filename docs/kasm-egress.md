# Remote browser egress through the lighthouse

The remote browser host (Kasm Workspaces on its own VM) sends **all** of its internet traffic out through the Nebula
lighthouse, so browsing sessions appear from the VPS address (203.0.113.10) rather than the home connection. If the tunnel
drops, the VM loses the internet instead of leaking through the home line. Inbound, the home LAN can only reach the Kasm
proxy from Traefik.

Scripts: [`edge/kasm-egress/`](../edge/kasm-egress/).

## Pieces

| Where | What | Script |
|---|---|---|
| Lighthouse | Cert re-signed with the **same key** plus `-unsafe-networks 0.0.0.0/0`; Nebula inbound rule `host: kasm, local_cidr: 0.0.0.0/0`; `ip_forward=1`; ufw `route allow in on nebula1 out on eth0 from <kasm overlay IP>`; MASQUERADE + TCPMSS clamp (`nebula-egress-nat.service`). Only that one host may exit. | `10-lighthouse.sh` |
| Kasm VM | Nebula node with `listen.so_mark: 16962` (0x4242) and `unsafe_routes` `0.0.0.0/1` + `128.0.0.0/1` via the lighthouse; policy rule `fwmark 0x4242 → table 200` (LAN gateway) so Nebula's own UDP never enters the tunnel; DNS 1.1.1.1 / 9.9.9.9 | `20-kasm-vm.sh` |
| Kasm VM | **Kill switch** `KASM-EGRESS` (OUTPUT + DOCKER-USER, IPv4 + IPv6): on the LAN interface only LAN destinations, DHCP and mark-0x4242 packets may leave | `20-kasm-vm.sh` |
| Kasm VM | **Inbound filter** `KASM-INGRESS` (INPUT + DOCKER-USER): from the LAN only tcp/443 from Traefik (matched on `ctorigdstport`, before DNAT), Nebula udp/4242, DHCP, ICMP. SSH moves to the overlay. | `30-kasm-ingress.sh` |

## Things that bite

- **netplan / systemd-networkd deletes routing rules and tables it does not own** whenever it reconfigures (`netplan apply`,
  DHCP renew). Declare the table-200 routes and the fwmark rule in the netplan file itself, not only in a script.
- **Unsafe routes need the remote cert to allow them.** The lighthouse cert must list `0.0.0.0/0` in its unsafe networks, and
  since Nebula 1.9 the lighthouse's inbound rule needs `local_cidr`. Re-signing with `-in-pub` (public key extracted from the
  existing cert) keeps the lighthouse's key, so only `host.crt` changes and a `systemctl reload nebula` picks it up.
- **Docker containers egress through FORWARD**, not OUTPUT, so the kill switch and inbound filter hook `DOCKER-USER`; a docker
  drop-in re-applies them after the daemon restarts.
- **Pin the Nebula listen port** (4242) on a host with an inbound filter, so the filter can allow it explicitly.
- Rollback timers that someone has to disarm in time are a poor safety net; the scripts apply, verify, and keep `--undo`
  (VM side) / the backups (lighthouse side) for a manual rollback. Enable the QEMU guest agent first, so
  `qm guest exec <vmid> -- …` from the hypervisor is a way back in if SSH is filtered off.

## Verify

```sh
# on the Kasm VM
curl -s https://api.ipify.org                      # -> the lighthouse's public address
docker run --rm --entrypoint sh <a kasm image> -c 'curl -s https://api.ipify.org'   # sessions too
systemctl stop nebula; curl -s -m8 https://api.ipify.org || echo blocked; systemctl start nebula   # kill switch
# from another LAN host
nc -zw3 <kasm LAN IP> 22 || echo "ssh closed on the LAN (use the overlay address)"
```
