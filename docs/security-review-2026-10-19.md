# Security review 2026-10-19: status of every finding

The review covered commit `394899f` with a static read of the whole repo. Every finding was worked through on
2026-10-19. **Done** means the kit now does what the review asked. **Partial** means the rest is listed under Open below.
Nothing here was tested against a fresh install yet: run the full kit on a throwaway host plus VPS before relying
on it (the Authentik hand-over and the SSH lock-down in particular).

| ID | Finding | Status | Where |
|---|---|---|---|
| H-1 | Identity headers trusted from the whole docker network; dash ungated | Done | Traefik has a fixed address on `proxy` (`TRAEFIK_PROXY_IP`); HortusFox / Navidrome / ArchiveBox / Calibre-Web / Actual / Nextcloud trust that /32 only. The desktop requires a gate header only Traefik adds (`DASH_GATE_SECRET`), plus a username on `/api/*`. |
| H-2 | Recovery skipped MFA, re-activated suspended users | Done | `authentik/recovery-flow.py`: MFA validation at 35 (skip if no device) and 36 (admins: deny if no device), no re-activation, other sessions ended. Admins aren't pre-added to `flow-recovery-allowed`. |
| H-3 | Kit left SSH open to the internet | Done | `generic/vps/ssh-overlay-only.sh`: run by `22-vps-lighthouse.sh` and the installer's optional `vps.ssh_lock` once overlay SSH is proven. `90-verify.sh` checks it. |
| H-4 | Database dumps world-readable | Done | `70-backup.sh`: umask 077, dumps in `secrets/dumps/`, failures reported, dumps removed after restic. |
| H-5 | Installer logged kit secrets | Done | `HEARDOS_OUTPUT` values are masked before logging. No token echo in `40-authentik-stack.sh`. |
| H-6 | Secrets on command lines; `repr()` as shell quoting | Done (one residual) | Installer: 0600 env file instead of `env K=v`; Portainer body via file; passwords via `HEARDOS_PASSWORD_FILE` / stdin across the kit. Residual: `cscli console enroll` only takes its key as an argument. |
| H-7 | Personal data in the repo | Done | Synthetic bill text and placeholder IDs; **git history rewritten** (`git filter-repo --replace-text`). |
| M-1 | Non-expiring bootstrap token | Done | `heardos-automation` service account, 90-day token (`42-ak-app.py token rotate`); bootstrap token revoked and removed from the compose env. The group has full superuser rights (no narrower Authentik role covers what the kit edits). |
| M-2 | MFA not tied to admins; email counted | Done | `mfa-required-for-admins` OR'd with `flow-mfa-required` at order 31; no email class there; `user add --admin`. |
| M-3 | LAN paths plaintext | Partial | Portainer certificate pinned; direct app ports bound to 127.0.0.1; http OIDC redirects only with `LAN_HTTP=1`. **Open:** step-ca LAN TLS isn't in the kit yet. |
| M-4 | OnlyOffice ungated | Done | Authentik gate with editor-asset skip paths; meta-IP fetching off. |
| M-5 | Framing widened for every host | Done | Only `https://dash.<domain>` may frame apps (`office` also allows `docs`). Authentik, Vaultwarden, dash and hub keep their own headers. Per-app iframe `allow` lists. |
| M-6 | Unverified downloads | Done | Nebula pinned and SHA-256-checked (kit and installer); CrowdSec from its apt repo; apt key fingerprints checked; step-cli pinned. |
| M-7 | SSH host keys trust-on-first-use | Partial | Changed keys fail at once. BYO / OVH / home servers need a confirmed fingerprint (or `*_SSH_FINGERPRINT` in the plan). **Open:** seeding cloud-init host keys for Linode / Vultr / the VPS provider. |
| M-8 | Vultr root password in state | Done | Goes to the vault as `VPS_ROOT_PASSWORD`. |
| M-9 | Nebula: host could reach laptop; no revocation | Done | Per-role inbound rules, 1-year host certs, `20-nebula-ca.sh revoke/renew`, `pki.blocklist`; kasm `local_cidr` excludes the overlay. |
| M-10 | Over-privileged Cloudflare credential; broad deletes | Done | Zone-scoped `CF_API_TOKEN` (`edge/dns/cfapi.py`); every change is printed first and needs `--apply`; deletes filter by type and content. **Owner action:** revoke the old tunnel credential. |
| M-11 | Portainer first-admin race | Done | `--admin-password-file` from a generated secret. |
| M-12 | Traefik ingress / forwarded-header trust too wide | Done (Vaultwarden residual) | `:80` published on LAN + overlay addresses only; trusted IPs = lighthouse /32; `strip-authentik` clears every identity header at the entrypoint. Vaultwarden has no trusted-proxy setting (rate limits only). |
| M-13 | paperless-gpt superuser | Done | Plain user with document / tag / correspondent permissions. |
| M-14 | Supply chain | Done | Hashed lockfile, SHA-pinned actions, least-privilege workflow permissions, build provenance; dash lockfile + `npm ci` + pinned base image. |
| M-15 | Budget visible to every desktop user | Done | `/api/summary` filtered by `app-actual-budget` / `app-mealie` / `app-homebox`. |
| M-16 | Restic password only inside the backup | Done | Emitted once and stored in the installer vault; restore pre-check. |
| M-17 | Container hardening | Partial | Critical images digest-pinned, docker-socket-proxy for Traefik, `no-new-privileges` / `cap_drop` / memory limits where images allow. Images that need setuid at start are listed in their compose files. |
| M-18 | Unredacted failure messages | Done | Masked before state / UI. |
| L-1 … L-16 | Hygiene | Done | Query strings redacted in the access log; HSTS includeSubDomains; no public redirects to LAN targets; media group 2775; Listmonk router priority; feeds importer as its own user with bounded reads; OCR constant-time key check and bounded queue; parameterised SQL; narrower Invidious skip; private temp dirs; quoting / IP validation; plan file 0600 enforced; secret CLI via stdin; no secrets on stdout; dash errors / throttle / CSP / postMessage scheme check. |

## Open items

- **LAN TLS in the kit (M-3):** port `security/stepca-*.sh` and the LAN wildcard into `generic/`, then drop the http
  redirect URIs entirely.
- **Cloud-init host keys (M-7)** for Linode / Vultr / the VPS provider, once each provider's user-data support is verified live.
- **Vaultwarden on its own network (M-12):** this would make `IP_HEADER` unforgeable from other containers.
- **Traefik 3.3 is end of life:** move to 3.7.x after a test install.
- **Existing hosts** must recreate the `proxy` network with a fixed subnet before Traefik gets its static address
  (`10-host-bootstrap.sh` prints the commands).
