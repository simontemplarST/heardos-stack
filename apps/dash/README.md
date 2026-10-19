# HeardOS desktop (`dash`)

The family's apps in one place, already signed in: a desktop in the browser with windows, a taskbar, a Start menu,
today's widgets, and a System window for whoever looks after it. One small Node container, no build step for the
front end, one dependency (`@actual-app/api`).

| Where | What |
|---|---|
| `server.js` | The whole backend: gate, `/api/*`, data sources, catalogue validation, diagnostics, static files |
| `apps.json` | The app catalogue: what shows on the desktop, for whom, where it opens |
| `public/desktop.js` + `desktop.css` + `index.html` | The desktop itself |
| `public/week.html` + `week.js` | The **This week** app (meals, shopping, budget, inventory) |
| `fetch-icons.mjs` | Image build step: downloads each app's icon from [dashboard-icons](https://github.com/homarr-labs/dashboard-icons) |
| `test/server.test.mjs` | `npm test`: gate, filtering, diagnostics, templating, headers |

How the sign-in works, the gate header and the framing rules are in [`docs/web-desktop.md`](../../docs/web-desktop.md).

## Settings (environment)

| Variable | Needed? | What it does |
|---|---|---|
| `DOMAIN` | yes | The family domain (`example.com`). Fills `{domain}` in `apps.json`, the pages and the brand image links. |
| `DASH_GATE_SECRET` | yes | Random secret Traefik's `dash-gate` middleware adds as `X-Dash-Gate`. Without it any container on the proxy network can reach the desktop (the container warns and keeps refusing `/api/*` without a username). |
| `SUPPORT_EMAIL`, `SUPPORT_NAME` | recommended | Who Help tells people to contact ("ask Alice"). The email button in Help pre-fills a message with the details. |
| `DASH_ADMIN_GROUP` | no | Authentik group that sees the **System** app and `/api/diagnostics`. Default `authentik Admins`. |
| `MEALIE_URL`, `MEALIE_TOKEN`, `MEALIE_PUBLIC_URL` | for the widgets | Mealie API (internal URL, an API token) and the address people open. |
| `ACTUAL_URL`, `ACTUAL_SYNC_ID`, `ACTUAL_PASSWORD`, `ACTUAL_PUBLIC_URL`, `ACTUAL_DATA_DIR` | for the widgets | Actual Budget. `ACTUAL_SYNC_ID` is the budget's *group id*; `ACTUAL_PASSWORD` is a **session token** (the web app's `user-token` in Local Storage) because the sync server is OpenID-only. Cache under `ACTUAL_DATA_DIR` (default `/data/actual`). |
| `HOMEBOX_URL`, `HOMEBOX_API_KEY`, `HOMEBOX_PUBLIC_URL` | for the widgets | Homebox API. |
| `CACHE_SECONDS` | no | How long the summary is cached for everyone (default 180). `?refresh` bypasses it at most once a minute. |
| `APPS_FILE` | no | Path of the catalogue (default the baked-in `apps.json`). Mount a file here to change apps **without rebuilding**. |
| `DASH_LOG_LEVEL` | no | `debug`, `info` (default), `warn`, `error`. |
| `PORT`, `LISTEN_HOST`, `TZ` | no | Defaults `8080`, `0.0.0.0`, image default. |

Every source is optional: a missing one shows "not set up yet" in This week and no widget; a failing one shows
"not answering right now" and is logged, and the desktop keeps working.

## The catalogue (`apps.json`)

```json
{
  "folders": ["Everyday", "House & paperwork", "Family", "Account", "Admin"],
  "apps": [
    {"id": "photos", "name": "Photos", "description": "Our pictures and videos", "folder": "Everyday",
     "icon": "immich", "url": "https://photos.{domain}/", "access": "immich"}
  ]
}
```

| Field | Meaning |
|---|---|
| `id` | Short unique name; also used by `/?open=<id>` launcher links and in people's pins/recents |
| `name`, `description` | What people see (keep the description one plain sentence: it's the tooltip and the Start menu line) |
| `folder` | Grouping on the desktop and in Start; must be one of `folders` |
| `icon` | A dashboard-icons name (`immich`, `mealie`, …), fetched at build time, or `builtin:<name>` for `public/icons/<name>.svg` |
| `url` | Where the app opens; `{domain}` is replaced. `/week.html` is the built-in This week app |
| `access` | Authentik app slug: only people in group `app-<slug>` see it. `null` = everyone signed in |
| `embed` | `false` = opens in a browser tab, never in a window (Authentik and Vaultwarden must stay `false`) |
| `allow` | The window's iframe permissions; default `fullscreen; clipboard-write; publickey-credentials-get`. Grant more only where an app needs it |
| `w`, `h` | Default window size in pixels |

**Adding an app:** add an entry, make sure the Authentik app and its `app-<slug>` group exist, then either rebuild the
image (`fetch-icons.mjs` downloads the icon) or, if you mounted `APPS_FILE`, just save the file: it is re-read on
every request and the System window lists anything wrong with it (duplicate ids, unknown folders, missing icons,
unknown fields). A broken entry is skipped, never fatal.

## Running, building, deploying

```bash
npm ci && npm test            # the server tests (needs build tools once: better-sqlite3 compiles)
npm run check                 # syntax of every script
docker build -t heardos-dash:local .   # multi-stage: builds native deps, then a slim runtime image
docker run --rm -p 8080:8080 -e DOMAIN=example.com -e DASH_GATE_SECRET=x heardos-dash:local
curl -H 'X-Dash-Gate: x' -H 'X-authentik-username: me' -H 'X-authentik-groups: app-dash' localhost:8080/api/apps
```

In the kit, `50-app.sh dash` builds the image, generates `DASH_GATE_SECRET`, writes the stack `.env` and deploys
[`generic/stacks/dash/compose.yml`](../../generic/stacks/dash/compose.yml). To roll back a bad release, point the
stack at the previous image tag and redeploy; nothing is stored in the container (people's window layouts and
preferences live in their own browser).

## When something is wrong

Open **System** (admins see it under *Admin*, or Help → *Open System*). It shows a checklist with a fix hint for
every unmet requirement, each data source's last success and last error, catalogue warnings and the last 30 errors.
`docker logs dash` has the same, one timestamped line per event.

| Symptom | Usually | Do |
|---|---|---|
| Everyone gets a 403 page | `X-Dash-Gate` isn't reaching the container | Check the Traefik `dash-gate` middleware is on the dash router *after* `authentik-fwd`, and that both sides have the same `DASH_GATE_SECRET` |
| "You've been signed out" right after signing in | `/api/*` saw no `X-authentik-username` | The dash router must carry `authentik-fwd`; forward-auth must list the `X-authentik-*` response headers |
| An app shows its own sign-in page inside the window | Session expired, or the app isn't same-site | Press ⟳; otherwise ↗ to open it in a tab. Apps must be on `*.<DOMAIN>` for the cookie to be shared |
| A window says "refused to connect" | The app forbids framing | The lighthouse's framing rule must allow `https://dash.<domain>` for that host (see `docs/web-desktop.md`) |
| Budget widget missing, System says Actual "unavailable: invalid or expired session token" | `ACTUAL_PASSWORD` expired | Copy a fresh `user-token` from Actual's web app (DevTools → Application → Local Storage) into the stack env and redeploy |
| An icon shows as a lettered tile | The icon name in `apps.json` isn't in dashboard-icons, or the image was built offline | Fix the name and rebuild, or use `builtin:` with your own SVG |
| Someone can't see an app that exists | They aren't in `app-<slug>` | Add them to the group in Authentik; the desktop picks it up on the next load |

Asking a family member for details: Help → **Copy details for support** copies the version, time, browser, open
windows and the apps they can see.
