# Plants, newsletters and saved pages

| App | Address | Sign-in | Notes |
|---|---|---|---|
| HortusFox | `plants.<domain>` | Authentik gate; the app signs people in from `X-authentik-email` / `-username` (auto-signup) | No LAN route: an app that trusts identity headers must only be reachable through the gate. |
| ArchiveBox | `archive.<domain>` | Authentik gate; `REVERSE_PROXY_USER_HEADER=X-authentik-username` from the proxy subnet only | `safe-onedomain-nojsreplay` (one domain, archived JavaScript not replayed); nothing public; archive.org submission off. `ALLOWED_HOSTS` must include `admin.archivebox.localhost` for the image's own healthcheck. |
| Listmonk | `news.<domain>` | Public subscribe / archive pages; `/admin` and `/api` (except `/api/public`) behind the gate, then Listmonk's own OIDC | Settings via `63-post-install.py listmonk-config`. |

## Listmonk API gotchas (v6)

- Basic auth with the admin password → 403. Log in through `/admin/login` (form with a nonce) and reuse the session cookie.
- `GET /api/settings` masks secrets. **Every `PUT` must re-supply both the SMTP password and the OIDC client secret**, or
  the mask string is stored and sign-in silently breaks.
- The OIDC button is a POST form to `/auth/oidc`.

## HortusFox: recurring tasks need cron

Recurring tasks (e.g. *water every 7 days*) only come back when `/cronjob/tasks/recurring` is called;
`63-post-install.py hortusfox-cron` sets the cron password and an hourly root cron entry. Other notes:

- Locations can't be created over the API; custom attribute types are `bool`, `int`, `double`, `string`, `datetime`.
- The API and cron controllers skip proxy auth and use their own token / password.
- [`apps/hortusfox/plant-template.py`](../apps/hortusfox/plant-template.py) builds a "Plant template" (care fields,
  watering notes, custom attributes for interval / amount / soil checks, a recurring watering task) to clone per plant.
