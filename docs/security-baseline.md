# HeardOS security baseline

Audit started 2026-10-15 13:48 (session 3b97f251), continued and written up 2026-10-15 (session f8191e71).
Scope: the the VPS provider lighthouse VPS (public edge), the Proxmox host `prox`, LXC 108 (Runtipi + every app),
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
| MFA | superusers must have a passkey/WebAuthn device (akadmin is exempt: service account, unusable password, API-only); `heardos-passwordless` flow available; `default-authentication-mfa-validation` stage in the login flow | list devices per user |
| Caddy | HSTS; `X-Content-Type-Options nosniff`; `Referrer-Policy strict-origin-when-cross-origin`; `-Server`; admin API on 127.0.0.1 only | Caddyfile in the kit |
| Mail | No mail server in the stack (Stalwart removed 2026-10-15). Outbound only, through Resend: Authentik → smtp.resend.com:587 STARTTLS with a send-only key; DKIM `resend._domainkey.example.com`, return path `send.example.com`; apex SPF stays `v=spf1 -all`, DMARC target `p=reject; adkim=s; aspf=r; rua=mailto:admin@example.net` (live 2026-10-15; reports also need the `example.com._report._dmarc.example.net` authorization TXT on the other zone); nothing listens for inbound mail | `dig TXT`, `~/heardos-install/mail/resend-dns.sh` |
| DNS | every A record points at 203.0.113.10 or at a host we still own; delete records for retired services the same day the service dies | zone listing in `security-check.sh` |
| Laptop | `credentials.env` and `~/.config/nebula-ca/*` mode 0600; kit files hold no literal secrets (they read `credentials.env` or generate at install) | `stat`, grep in the check |

## Findings and status

Done = applied and verified. Pending = blocked in the autonomous session; a script or command is given.

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | prox allowed SSH password auth and `PermitRootLogin yes` | high | Done (baseline sshd drop-in) |
| 2 | CT 108 allowed SSH password auth | high | Done |
| 3 | 67 (VPS) / 162 (prox) / Docker (CT 108) packages unpatched; prox had enterprise + bookworm repos erroring | high | Done; VPS rebooted into 6.8.0-147 on 2026-10-15 |
| 4 | Traefik API + dashboard reachable unauthenticated on the shared Docker network | medium | Done |
| 5 | Authentik default password policy (8 chars, no breach check) | medium | Done (12 chars, HIBP, zxcvbn 3) |
| 6 | No SPF/DMARC; `mm.example.com` dangling A record | medium | Done (TXT records added, mm deleted) |
| 7 | rpcbind listening on prox with no NFS in use | low | Done (masked) |
| 8 | Runtipi error telemetry on | low | Done |
| 9 | Mealie and Baïkal published LAN host ports | low | Done; the Mealie crash loop it caused was fixed 2026-10-15 (`ALLOW_SIGNUP` pinned in the override) |
| 10 | Paperless :8012 and Homebox :7745 published on the LAN | low | Done 2026-10-15 (`close-lan-ports.sh`). **Caused an outage**: the first run also set `exposedLocal=false`, which removes the Traefik labels and made paperless/homebox.example.com fall through to the Runtipi dashboard for ~1 h; fixed by re-enabling local exposure. Rule: only ever change `openPort`. |
| 11 | Caddy sends only HSTS; `Server` header leaks | low | Done 2026-10-15 (`caddy-headers.sh`) |
| 12 | `akadmin` is a superuser with no MFA, last login 2026-09-29, and owns the automation API token | high | **Interactive login disabled 2026-10-15**: type → `service_account`, password set unusable via `ak shell` (no MFA devices, so the passkey flow cannot be used either); still active + superuser so the API token works. Optional follow-up: expiring token on a fresh service account (`security/authentik-hardening.md`) |
| 13 | `carol` is a superuser (member of `authentik Admins`) | medium | **Decide**: intended? If not, remove from the group |
| 14 | `bob`, `dave` have no MFA device; `dave` has never logged in | medium | Enrol passkeys from the user portal; the MFA stage passes users with no device |
| 15 | Automation token `claude-setup-20260928` never expires | low | Recreate as expiring (90 d) under the service account from #12 |
| 16 | OAuth providers carry `http://` redirect URIs for sslip LAN names and `http://homebox.example.com` | low | Done 2026-10-15: public `http://homebox.example.com/...` removed; LAN sslip ones kept by design |
| 17 | Immich allows password login beside OAuth (`passwordLogin: true`) | low | Accepted: local admin is the break-glass; mobile uses OAuth |
| 18 | root@pam on prox has no TFA | medium | Enrol TOTP in the PVE UI (needs the password); `alice@authentik` is the daily login |
| 19 | CT 108 LAN host ports: Authentik 9000/9443, Immich 2283, HA 8123 + go2rtc 18555, Traefik 80/443 (Stalwart's mail ports gone with its removal on 2026-10-15) | info | Accepted: LAN-only, each has its own auth, several are used by phones on the LAN |
| 20 | `dco.example.com` → 198.51.100.11 and `forge.example.com` → 198.51.100.10 (not the lighthouse) | info | Your other other VPS hosts; delete the records when those VMs go |

## Lessons folded in from memory

- Runtipi `update-config`: never set `exposedLocal=false` on an app whose public Traefik router uses an `@docker` service (the labels vanish and the public host falls through to the dashboard); the durable fix is file-provider services by container name for every public router.
- Runtipi `update-config` rewrites `app.env` from the store form: send **every** form field with correct types
  (booleans as booleans) **at the top level of the JSON body** (a nested `form` object is silently ignored, which is
  how Mealie's variable went blank and crash-looped on 2026-10-15). Prefer the user-config override for anything
  the form does not own.
- Nebula ≥ 1.9 firewall rules match only the node's own overlay IP unless `local_cidr` is set; `groups: [a, b]`
  means both groups. SSH on the lighthouse is socket-activated: bind with a systemd drop-in, not sshd_config.
- Cloudflare: account tokens cannot touch the example.com zone; only the tunnel `cert.pem` token on CT 108 can.
  Audit logs are unreachable by API, so DNS changes must be logged in memory by hand.
- A single Immich transcode can starve Authentik on CT 108 (load 30+). Availability is part of the posture:
  keep the Immich CPU caps and the Nebula CPU priority drop-in.
- The LAN DNS intercept caches NXDOMAIN for 30 min; verify new records from the lighthouse.
- Auto-mode classifier: changes to shared family services and the public edge get refused mid-task.
  Stage files, validate, and hand the final swap to a human when that happens.

## CrowdSec on the lighthouse (2026-10-17)

CrowdSec agent + nftables firewall bouncer ([`security/crowdsec-lighthouse.sh`](../security/crowdsec-lighthouse.sh)).
Caddy writes a JSON access log (snippet `(accesslog)` in [`edge/Caddyfile`](../edge/Caddyfile), rolled at 50 MiB × 5);
collections caddy, linux, sshd, http-cve, base-http-scenarios; the Nebula overlay is whitelisted so the admin can never be
banned. The package's automatic bouncer registration produced "access forbidden" — re-add the bouncer key with
`cscli bouncers add`. Optional: enrol in the free CrowdSec console for shared blocklists. Fresh installs: `generic/33-vps-crowdsec.sh`
(kit, `CS_ENROLL_KEY` optional) or the installer's *Block known attackers* option; `generic/lib/caddyfile.py` always
writes the access log.
