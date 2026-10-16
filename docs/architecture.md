# Architecture

## Hosts

| Host | Where | Role | Reachable on |
|---|---|---|---|
| `nebula-lh` | VPS, Region-1 | Caddy public ingress, Nebula lighthouse + relay | 80, 443 (tcp+udp) public; 22 only on the overlay (10.42.0.1) |
| `prox` | Home, Proxmox VE 9 | Runs the app container; Nebula node; routes the home LAN for the `admins` group | LAN; overlay 10.42.0.10 |
| LXC 108 `runtipi` | On prox, unprivileged | Runtipi v4: Traefik, Authentik, every app | LAN 192.168.10.20; overlay 10.42.0.11 |
| Laptop | Anywhere | Operator: holds the Nebula CA and `credentials.env` | overlay 10.42.0.20 |

## A request, end to end

1. **DNS.** Every app hostname is an unproxied A record pointing at the lighthouse. Cloudflare only serves DNS.
2. **Caddy** terminates TLS (Let's Encrypt, HTTP-01), adds HSTS, nosniff and a referrer policy, drops the Server
   header, and reverse-proxies to `10.42.0.11:80` across the Nebula overlay with X-Forwarded-Proto/Port set. Large
   uploads are unlimited; there is no CDN in front and so no upload cap.
3. **Nebula** carries the packet to the app container. The container's Nebula firewall only accepts tcp/80 from the
   `lighthouse` group, so Traefik is not reachable from the internet by any other path.
4. **Traefik** matches the hostname with a file-provider router (`host/traefik/public-example.yml`). Routers point at
   containers by name, never at the Docker provider, because Docker-provider services vanish whenever a container's
   health check is starting and the request would fall through to the Runtipi dashboard.
5. **Authentik** is consulted one of two ways: apps that speak OIDC (Immich, Nextcloud, Paperless, Mealie, Home
   Assistant, Vaultwarden, Actual, Wallos, Homebox, Proxmox) redirect to it; apps that do not (Navidrome, Baïkal,
   Invidious, Homepage, CronMaster, OnlyOffice, Calibre-Web, the dashboard) sit behind Traefik forward auth to the
   embedded outpost, which also passes identity headers to the app.
6. **The app** answers. Apps reach each other by container name on the Runtipi network; LAN host ports are published
   only where phones on the LAN need them (Immich, Home Assistant, Authentik).

## The front door

`https://cloud.<domain>/<slug>` is a Caddy site on the lighthouse that answers with a 302 to the app's real host, keyed
by the Authentik application slug (and by the public subdomain name as an alias). `/` and unknown slugs go to the
Authentik application library. Authentik's launch URLs, the Homepage tiles and the dashboard links all use it. The same
site serves the brand assets at `/brand/*` for the email templates. See [front-door.md](front-door.md).

## Identity

Authentik holds users and groups. Access to each application is a policy binding to an `app-<slug>` group. Behaviour
is a second set of groups prefixed `flow-` (see [authentik-flows.md](authentik-flows.md)). Superusers must hold a passkey.
The stock `akadmin` account is kept only as the owner of the automation token: it is a service account with an unusable
password and cannot log in interactively.

## Mail

No mail server. Authentik is the only sender; it submits to Resend on 587 with a send-only key. DKIM is signed with the
apex domain, the return path is a Resend subdomain, DMARC is `p=reject` with reports to the operator. See
[outbound-mail.md](outbound-mail.md).

## Admin access

- SSH to the lighthouse only over the overlay (`ssh root@10.42.0.1`).
- SSH to prox and the app container over the LAN, or from anywhere over the overlay once the LAN route is installed
  on the laptop ([lan-over-nebula.md](lan-over-nebula.md)).
- Proxmox's web UI accepts the Authentik OIDC realm; `root@pam` stays as break-glass.
- Runtipi's API (`host/tipi.py`) is used for app lifecycle: install, restart, update-config, custom apps.

## What is deliberately not here

- No Cloudflare proxy, tunnel or WAF: it capped uploads at 100 MB and hid client IPs. The lighthouse replaced it.
- No inbound mail: nothing listens on 25.
- No single-hostname layout: most of these apps cannot be served under a path prefix, so each keeps its own host and
  the front door redirects.
