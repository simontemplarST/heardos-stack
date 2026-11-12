# The edge host runs Beacon (ngfw-lighthouse)

Since 2026-11-12 the lighthouse/relay on the public edge host is **Beacon**, the standalone Nebula node of the ngfw
project (binary `ngfw-lighthouse`), instead of the hand-run `nebula` unit. Peers see no change: it uses the same host
key and certificate, address, port and interface name (`nebula1`, because the host's ufw and nft rules name it).

- `beacon-lighthouse.json` is its `/etc/ngfw-lighthouse/config.json`. It mirrors `nebula-lighthouse.yml` exactly:
  the same inbound rules (ICMP; SSH from one named host and from the `admins` group; one named host allowed to forward
  anything through the node, `local_cidr: 0.0.0.0/0`), `default_local_cidr_any: false`, `use_relays: false` and the
  socket tuning. The rendered Nebula file was diffed against the hand-written one section by section.
- `nebula-lighthouse.yml` and `nebula.service` stay on the host, disabled, as the rollback.
- Swap (a few seconds of rendezvous; established tunnels persist), run detached so the SSH session that rides the
  overlay can drop, with a dead-man's rollback timer armed first:

  ```
  systemd-run --unit=beacon-swap-rollback --on-active=240s /bin/sh -c \
    'systemctl stop ngfw-lighthouse; systemctl disable ngfw-lighthouse; systemctl enable --now nebula'
  systemd-run --unit=beacon-swap /bin/sh -c \
    'systemctl disable nebula; systemctl enable ngfw-lighthouse; systemctl stop nebula; systemctl start ngfw-lighthouse'
  # verify peers and reachability, then:
  systemctl stop beacon-swap-rollback.timer
  ```
- Identity: put the existing `host.key` in `/etc/ngfw-lighthouse/pki/`, then `ngfw-lighthouse install-certs ca.crt host.crt`
  (it refuses a pair that does not verify); `ngfw-lighthouse check` validates the config.
- The `nebula-status` forced command on the host reads the Beacon unit and refuses edits of the dormant config.
