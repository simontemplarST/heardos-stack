# HeardOS security baseline

First audit 2026-10-15. Scope: the lighthouse VPS (public edge), the Proxmox host `prox`, LXC 108 (Runtipi + every app),
Authentik, DNS, and the laptop that holds the CA and credentials. `security/security-check.sh` re-runs
the read-only checks and prints PASS/WARN per line; re-run it after any infrastructure change.

## Threat model (what we actually defend against)

- Internet: only the lighthouse is reachable (Caddy 80/443, Nebula 4242/udp). Everything else sits behind
  Authentik SSO or an app's own login. No Cloudflare in front since 2026-10-11, so Caddy + fail2ban + the
  apps themselves are the whole public surface.
- Family LAN 192.168.10.0/24: treated as semi-trusted. LXC 108 publishes a handful of host ports there
  (see "Accepted" below). Since 2026-10-14 the Nebula `admins` group can also reach the LAN from anywhere
  through prox, so the LAN is only as private as the laptop's Nebula key.
- The laptop is the root of trust: Nebula CA, `~/.config/credentials.env`, SSH key. Losing it = rebuild
  from the kit; leaking it = everything.

## Baseline posture (keep these true)

| Area | Rule | How enforced |
|---|---|---|
| SSH everywhere | key-only, root `prohibit-password`, no password or keyboard-interactive auth | VPS: ssh.socket bound to 10.42.0.1 only; prox + CT 108: `/etc/ssh/sshd_config.d/90-heardos-baseline.conf` |
| VPS firewall | ufw deny-in; allow 80, 443 (tcp+udp), 4242/udp, 22 only on `nebula1` | `ufw status`; fail2ban sshd jail active |
| VPS updates | unattended-upgrades on; kernel reboots applied promptly (edge host, ~60 s outage) | `/var/run/reboot-required` must not linger |
| prox | PVE no-subscription repo only, enterprise + stale bookworm lists disabled; unattended-upgrades for Debian security; rpcbind masked (no NFS); pve-firewall stays off (LAN host) | `apt-get update` clean, `ss -ltn` shows only 22, 3128, 8006 |
| CT 108 | unprivileged, nesting only; Traefik API/dashboard off (`api.insecure: false`); Runtipi `ALLOW_ERROR_MONITORING=false`; apps reach each other by container name, never by LAN host port | traefik.yml, `/root/runtipi/.env` |
| Authentik | password policy 12+ chars, HIBP, zxcvbn ≥ 3; every application has an `app-<slug>` group policy binding (nothing open to all users); OAuth providers confidential (Paperless public for the iOS app by design); no `http://` redirect URIs on public hostnames; API tokens named and owned by a known account | `security-check.sh` prints bindings and redirect URIs |
| MFA | every human account has a passkey/WebAuthn device, superusers first; `default-authentication-mfa-validation` stage in the login flow with "Not configured action" = **Configure**, so a user without a device is sent to enrolment instead of passing through; `akadmin` is a service account with an unusable password (API-only); `heardos-passwordless` flow available | list devices per user |
| Caddy | HSTS; `X-Content-Type-Options nosniff`; `Referrer-Policy strict-origin-when-cross-origin`; `-Server`; admin API on 127.0.0.1 only | Caddyfile in the kit |
| Mail | No mail server in the stack (Stalwart removed 2026-10-15). Outbound only, through Resend: Authentik → smtp.resend.com:587 STARTTLS with a send-only key; DKIM `resend._domainkey.example.com`, return path `send.example.com`; apex SPF stays `v=spf1 -all`, DMARC target `p=reject; adkim=s; aspf=r; rua=mailto:admin@example.net` (live 2026-10-15; reports also need the `example.com._report._dmarc.example.net` authorization TXT on the other zone); nothing listens for inbound mail | `dig TXT`, `~/heardos-install/mail/resend-dns.sh` |
| DNS | every A record points at 203.0.113.10 or at a host we still own; delete records for retired services the same day the service dies | zone listing in `security-check.sh` |
| Laptop | `credentials.env` and `~/.config/nebula-ca/*` mode 0600; kit files hold no literal secrets (they read `credentials.env` or generate at install) | `stat`, grep in the check |

## What the audit checks

`security/security-check.sh` covers these; `security/authentik-hardening.md` has the fix for each Authentik item.

| Check | Severity if it fails | Fix |
|---|---|---|
| SSH password auth or `PermitRootLogin yes` on any host | high | baseline sshd drop-in |
| Pending security updates or a lingering `reboot-required` | high | unattended-upgrades; reboot the edge in a quiet window |
| Traefik API/dashboard reachable without auth | medium | `api.insecure: false` |
| Authentik stock password policy | medium | 12+ chars, HIBP, zxcvbn ≥ 3 |
| Stock `akadmin` usable interactively, or owning a non-expiring token | high | service account + expiring token (`authentik-hardening.md`) |
| A superuser nobody intended | medium | remove from `authentik Admins` |
| Users without an MFA device, or an MFA stage that skips them | medium | enrol passkeys; set the stage to Configure |
| Proxmox `root@pam` without TFA | medium | enrol TOTP in the PVE UI; use the OIDC realm daily |
| `http://` OAuth redirect URIs on public hostnames | low | delete them (LAN-only sslip ones are fine) |
| Apps publishing LAN host ports they don't need | low | change only `openPort`/the port mapping, never the Traefik exposure |
| Missing security headers, `Server` header | low | `caddy-headers.sh` |
| SPF/DMARC missing; A records for retired services | medium | TXT records; delete dangling records the day a service dies |

## Lessons learned

- Runtipi `update-config`: never set `exposedLocal=false` on an app whose public Traefik router uses an `@docker` service (the labels vanish and the public host falls through to the dashboard); the durable fix is file-provider services by container name for every public router.
- Runtipi `update-config` rewrites `app.env` from the store form: send **every** form field with correct types
  (booleans as booleans) **at the top level of the JSON body** (a nested `form` object is silently ignored, which is
  how Mealie's variable went blank and crash-looped on 2026-10-15). Prefer the user-config override for anything
  the form does not own.
- Nebula ≥ 1.9 firewall rules match only the node's own overlay IP unless `local_cidr` is set; `groups: [a, b]`
  means both groups. SSH on the lighthouse is socket-activated: bind with a systemd drop-in, not sshd_config.
- Cloudflare: account tokens cannot touch the example.com zone; only the tunnel `cert.pem` token on CT 108 can.
  Audit logs are unreachable by API, so log DNS changes by hand.
- A single Immich transcode can starve Authentik on CT 108 (load 30+). Availability is part of the posture:
  keep the Immich CPU caps and the Nebula CPU priority drop-in.
- The LAN DNS intercept caches NXDOMAIN for 30 min; verify new records from the lighthouse.
- Changes to shared family services and the public edge: stage files, validate, then make the final swap
  deliberately with a rollback ready.

## CrowdSec on the lighthouse (2026-10-17)

CrowdSec agent + nftables firewall bouncer ([`security/crowdsec-lighthouse.sh`](../security/crowdsec-lighthouse.sh)).
Caddy writes a JSON access log (snippet `(accesslog)` in [`edge/Caddyfile`](../edge/Caddyfile), rolled at 50 MiB × 5);
collections caddy, linux, sshd, http-cve, base-http-scenarios; the Nebula overlay is whitelisted so the admin can never be
banned. The package's automatic bouncer registration produced "access forbidden" — re-add the bouncer key with
`cscli bouncers add`. Optional: enrol in the free CrowdSec console for shared blocklists. Fresh installs: `generic/33-vps-crowdsec.sh`
(kit, `CS_ENROLL_KEY` optional) or the installer's *Block known attackers* option; `generic/lib/caddyfile.py` always
writes the access log.

**Incident 2026-10-18: the home IP got banned, and every site went down.** `crowdsecurity/http-sensitive-files` plus
`http-crawl-non_statics` banned the home connection (55 events in a few minutes of normal family use). The trigger was
Actual Budget's web app, which fetches its own SQL migrations (`/data/migrations/*.sql`) on every load. The home
server's Nebula link to the lighthouse comes from that same IP, so the ban also cut Caddy → home, and every public
site returned 502 for everyone. Admin SSH to the lighthouse is overlay-only, so recovery meant joining a phone hotspot
to get a different IP, then running [`security/unban-home.sh`](../security/unban-home.sh). Since then:

- `heardos/app-paths-whitelist`: `budget.<domain>` + `/data/*` is exempt; everything else is still inspected.
- `heardos/home-whitelist`: the home public IP is never banned. Fresh installs pass `HOME_PUBLIC_IP=` to
  `generic/33-vps-crowdsec.sh`. If the ISP changes the address, update the file and `systemctl reload crowdsec`.
- Symptom to recognise: sites 502 from outside, and the lighthouse is silent (no ping) from home only.
