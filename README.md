# HeardOS

A family cloud: photos, recipes, documents, music, passwords, calendars, a home-automation hub and a few
household tools, all behind one sign-in, reachable from anywhere, with nothing but a single VPS on the public
internet. This repository is the set of scripts, configs, templates and documentation that build and operate it.

<p align="center"><img src="brand/png/lockup/heardos-horizontal-on-light-128h.png" alt="HeardOS" width="400"></p>

## How it is put together

```mermaid
flowchart LR
    subgraph internet[Internet]
        phones[Family phones & laptops]
        dns[(Cloudflare DNS<br/>A records → lighthouse)]
    end
    subgraph vps[Lighthouse VPS · only public host]
        caddy[Caddy<br/>TLS, HTTP/3, security headers<br/>cloud.&lt;domain&gt;/&lt;app&gt; front door]
        lh[Nebula lighthouse + relay<br/>udp/4242]
    end
    subgraph home[Home LAN 192.168.10.0/24]
        prox[Proxmox host<br/>Nebula node · LAN gateway for admins]
        subgraph ct[LXC 108 · Runtipi]
            traefik[Traefik<br/>public routers · forward auth]
            ak[Authentik<br/>SSO · MFA · flows · email]
            apps[Immich · Nextcloud · Paperless · Mealie<br/>Home Assistant · Vaultwarden · Navidrome<br/>Homebox · Baïkal · Actual · Wallos · more]
        end
    end
    resend[Resend<br/>outbound mail]
    phones -->|https| caddy
    phones -.->|Nebula, admins| lh
    dns -.-> phones
    caddy -->|Nebula overlay 10.42.0.0/24<br/>tcp/80| traefik
    lh <-.-> prox
    lh <-.-> ct
    traefik --> ak
    traefik --> apps
    apps -->|OIDC / forward auth| ak
    ak -->|smtp 587| resend
```

A detailed, annotated map of every host, route and app is in [`docs/network-map.svg`](docs/network-map.svg)
(interactive version: [`docs/network-map.html`](docs/network-map.html)).

**The shape of it**

- **One public host.** A small VPS runs Caddy for TLS and a Nebula lighthouse. Its only open ports are 80, 443 and
  udp/4242; SSH is reachable solely over the overlay. Everything else lives at home.
- **A private overlay.** [Nebula](https://github.com/slackhq/nebula) joins the VPS, the Proxmox host, the app container
  and admin laptops into 10.42.0.0/24. Caddy proxies every public hostname across it to Traefik at home, so no port is
  forwarded on the home router and the home IP never appears in DNS. Admins can also reach the whole home LAN through
  the Proxmox node (`nebula-lan/`).
- **One sign-in.** [Authentik](https://goauthentik.io) fronts every app, either as an OIDC provider or through Traefik
  forward auth. Behaviour is assigned by group membership: MFA required, suspended, 8-hour sessions, recovery allowed,
  self-service, explicit consent (`authentik/business-flows.py`). Emails are branded (`authentik/email-templates/`)
  and go out through Resend.
- **Apps on Runtipi.** The app host is an unprivileged Proxmox LXC running Runtipi. Each app is a Runtipi store app plus
  a small override in `host/runtipi-overrides/` that wires it to Authentik and to the shared data folders.
- **A front door.** `https://cloud.<domain>/<slug>` redirects to each app's real hostname, so people only need to
  remember one address; the family hub (Homepage) and the dashboard link through it.

## Repository layout

| Path | What it holds |
|---|---|
| `generic/` | A from-scratch rebuild kit for any x86-64 Ubuntu host + any VPS: numbered scripts (`10-host-bootstrap.sh` → `90-verify.sh`), compose stacks, a plan file (`heardos.env.example`). Start here if you want to build your own. |
| `edge/` | The lighthouse as it actually runs: `Caddyfile` (TLS, headers, the cloud front door, brand asset hosting), Nebula configs for lighthouse and Proxmox node, the node installer, Cloudflare DNS helpers, and `kasm-egress/` (full-tunnel egress, kill switch and inbound filter for the remote-browser VM). |
| `host/` | The app host: Traefik public/local router files, per-app Runtipi overrides (secrets redacted), the Runtipi API helper `tipi.py`, Nextcloud/Immich/calendar helpers. |
| `authentik/` | Group-driven business flows and their test harness, the recovery flow builder, launch-URL tool, the branded email kit and its render check, and the sign-in background generator. |
| `security/` | The written security baseline, a re-runnable read-only posture check, the external port scan, and the remediation scripts from the 2026-10 audit, the laptop firewalld tightening, CrowdSec for the edge (the kit installs it with `generic/33-vps-crowdsec.sh`; the GUI installer offers it as an option) and the step-ca LAN CA. |
| `mail/` | Resend set-up and DNS verification. |
| `nebula-lan/` | Reaching the home LAN over the overlay: router-side (Proxmox) and client-side (laptop) scripts. |
| `apps/` | Custom pieces: the family dashboard (`dash/`), Homepage hub config, the trimmed LibreSpeed page, Actual Budget tooling, the Actual → Wallos subscription sync, the cookbook importer, the per-app Hearth theme files (`brand-theme/`), the HortusFox plant template and the paperless-gpt CPU benchmark. |
| `brand/` | The HeardOS identity ("Hearth Crest"): SVG marks, lockups, icons, palette, the sign-in background. |
| `docs/` | Architecture, security baseline, flow and email documentation, operations notes, the Runtipi → Portainer migration plan. |

## Documentation

- [Architecture and request flow](docs/architecture.md)
- [Security baseline and audit findings](docs/security-baseline.md)
- [Authentik: group-driven flows](docs/authentik-flows.md)
- [Authentik: branded email kit](docs/email-kit.md)
- [Outbound mail through Resend](docs/outbound-mail.md)
- [The cloud front door](docs/front-door.md)
- [Home LAN over Nebula](docs/lan-over-nebula.md)
- [Remote browser egress through the lighthouse](docs/kasm-egress.md)
- [Skipping app login pages when already signed in](docs/sso-auto-redirect.md)
- [Trusted certificates for LAN names (step-ca)](docs/lan-ca.md)
- [Plants, newsletters and saved pages](docs/household-apps.md)
- [AI filing for Paperless on a CPU](docs/paperless-gpt.md)
- [One look across the apps](docs/hearth-theme.md)
- [Operating Runtipi: things that bite](docs/runtipi-notes.md)
- [Brand package](brand/README.md)

## Conventions you will see everywhere

- **Secrets never live in the repo.** Scripts read `~/.config/credentials.env` (mode 0600) on the operator's machine, or
  generate secrets at install time into the host's own secrets file. Anything that had a literal secret in the
  working copy is `<REDACTED>` here.
- **Idempotent scripts.** Nearly every script can be re-run; it finds what exists and only creates or re-asserts what
  is missing. Most have a `--dry-run`.
- **Verify, then trust.** Changes end with a check: a render, an HTTP probe, a posture check, a test user driven
  through a flow. `security/security-check.sh` is the standing one.
- **Placeholders stand in for the real site.** The configs are a real, running system with its identifiers swapped for
  documentation values: `example.com` for the public domain, `example.net` for the operator's own domain, people named
  alice/bob/carol/dave, `203.0.113.10` for the VPS, `192.168.10.0/24` for the home LAN, `<REDACTED>` for secrets and
  `<OIDC_CLIENT_ID>` / `<ID>` for opaque identifiers. Search and replace those to adapt; the `generic/` kit is the
  parameterised version built around a plan file.

## Status

Running since September 2026. The security audit of 2026-10-15 is written up in `docs/security-baseline.md`
with every finding and its state. Open items are listed there.

## License

MIT, see [LICENSE](LICENSE). The HeardOS name and the Hearth Crest mark are the owner's; use the scripts, not the brand.
