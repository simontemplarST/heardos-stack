# The HeardOS desktop

`dash.<domain>` is a desktop in the browser. Every app opens **already signed in**, in a window you can move, resize,
snap, minimise and maximise. There's a taskbar, a Start menu with search, desktop icons and widgets for today
(meals, shopping, budget, what's at home). It installs as an app (PWA), so on a laptop it runs in its own window with no
browser UI. On a phone every window is full screen.

Code: [`apps/dash`](../apps/dash) (Node, no build step). Stack: [`generic/stacks/dash`](../generic/stacks/dash/compose.yml).

## How "already signed in" works

Every app lives on the same registrable domain (`*.example.com`), so a window's `<iframe>` is *same-site*. The browser
sends the Authentik session cookie into it, and the app's forward-auth or OIDC redirect completes silently inside the
window. No tokens are passed between apps, and the desktop never sees a password.

```
browser ── dash.<domain> (desktop, behind the Authentik gate)
            └─ window = <iframe src="https://photos.<domain>/">
                 └─ same-site → Authentik cookie sent → already signed in
```

## Which apps a person sees

The gate in front of the desktop adds `X-authentik-username/-name/-email/-groups`. `GET /api/apps` returns the entries
of [`apps.json`](../apps/dash/apps.json) whose `access` slug is in the person's groups as `app-<slug>` (the same groups
Authentik uses to allow the app), plus the public ones (`access: null`). Because the desktop trusts those headers, **it
must have no route that skips the gate.** The stack has none, and must not get a LAN route.

### The gate header

Other containers share the `proxy` network and could call the desktop directly with forged `X-authentik-*` headers.
So the dash router adds one more middleware after `authentik-fwd`: `dash-gate`, a Traefik `headers` middleware that sets
`X-Dash-Gate: ${DASH_GATE_SECRET}` (a random secret `50-app.sh dash` keeps in `secrets.env`). `server.js` answers
**403** to every path except `/healthz` unless that header matches (constant-time compare), and every `/api/*` call
also needs a non-empty `X-authentik-username`. Traefik overwrites any `X-Dash-Gate` a client sends, so the secret is
never seen by a browser. If `DASH_GATE_SECRET` is unset the container logs a warning and still refuses `/api/*` without
a username, but treat that as a broken install.

### What the widgets show

`/api/summary` (Mealie, Actual Budget, Homebox) is cached once for everyone, then filtered per person: the budget needs
`app-actual-budget`, meals and shopping `app-mealie`, the inventory `app-homebox`. A section the person can't see
comes back as `{"error": "no access"}`, and This week and the desktop widgets simply leave it out. An upstream failure is
logged in the container and shown only as "unavailable". `?refresh` bypasses the cache at most once a minute.

`apps.json` fields: `id`, `name`, `description`, `folder` (Start menu and desktop grouping), `icon` (a
[dashboard-icons](https://github.com/homarr-labs/dashboard-icons) name, downloaded into the image at build time by
`fetch-icons.mjs`, or `builtin:<name>`), `url` (`{domain}` substituted), `access`, optional `embed: false` (opens in a
browser tab: Proxmox on the home network only, and **My account (Authentik) and Passwords (Vaultwarden), which are never
framed**), optional `allow` (the window iframe's permissions policy, see below), and an optional default window size
`w`/`h`.

### Per-app permissions (`allow`)

Each window's `<iframe allow="...">` comes from the app's `allow` field. The default is
`fullscreen; clipboard-write; publickey-credentials-get` (passkey sign-in works, nothing sensitive is delegated).
Only apps that need more get it:

| App | Extra permissions | Why |
|---|---|---|
| Documents (Nextcloud) | `clipboard-read` | paste inside OnlyOffice documents |
| Music (Navidrome) | `autoplay` | next track starts by itself |
| Videos (Invidious) | `autoplay; picture-in-picture` | video playback |
| Safe browser (Kasm) | `clipboard-read; autoplay` | clipboard sync with the remote browser |
| Home controls (Home Assistant) | `microphone` | Assist voice commands |

Camera, geolocation and screen capture are given to no app. Add them per app only when a feature needs it.

### Desktop page policy

The desktop's own pages send `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'
'unsafe-inline'; img-src 'self' https://cloud.<domain> data:; frame-src 'self' https:; connect-src 'self';
frame-ancestors 'self'; base-uri 'none'; form-action 'self'`. No inline scripts (This week's code is `week.js`); the
brand images come from `https://cloud.<domain>/brand` (`{domain}` in `index.html` and the manifest is filled in by
the server). A window's "open" request over `postMessage` is honoured only for `https:` addresses or the desktop's own
pages.

## The edge has to allow framing by the desktop

Several apps forbid being framed at all: Homebox (`X-Frame-Options: DENY`), and Nextcloud, Paperless and Home
Assistant (`SAMEORIGIN` / `frame-ancestors 'self'`). The lighthouse Caddy rewrites that, **only for the hosts the
desktop opens in windows**, so that the app itself and **only the desktop, `https://dash.<domain>`**, may frame it.
No other family site can frame another app. The kit's `generic/lib/caddyfile.py` renders it, roughly:

```caddyfile
reverse_proxy <home-overlay-ip>:80 {
	# ...
	header_down -X-Frame-Options
	header_down Content-Security-Policy "frame-ancestors 'self'" "frame-ancestors 'self' https://dash.example.com"
	header_down Content-Security-Policy "frame-ancestors 'none'" "frame-ancestors https://dash.example.com"
	header_down +Content-Security-Policy "frame-ancestors 'self' https://dash.example.com"
}
```

Exceptions:

- **`auth.<domain>` (Authentik) and `vault.<domain>` (Vaultwarden) are never framed.** They keep their own
  `X-Frame-Options` / `frame-ancestors`, and the desktop opens them in a browser tab (`embed: false`). A sign-in or
  vault page inside someone else's frame is exactly what clickjacking needs.
- **`dash.<domain>` (the desktop itself) and `hub.<domain>` keep their own headers too.** They are top-level pages the
  desktop never frames (the desktop allows only itself as a frame parent).
- **`office.<domain>` (OnlyOffice) also allows `https://docs.<domain>`**, because Nextcloud opens documents in an
  OnlyOffice frame: `frame-ancestors 'self' https://dash.<domain> https://docs.<domain>`.

Clickjacking protection stays in place: every site except the desktop (and Nextcloud, for OnlyOffice) is still blocked.

**Keep the quotes.** `'self'` and `'none'` must keep their single quotes. Unquoted, a browser reads `self` as a host
called "self", and the app can no longer frame itself. (The first live attempt lost them in shell quoting and broke
Nextcloud → OnlyOffice until it was fixed.)

## Using it

| Do | How |
|---|---|
| Open an app | Double-click its icon (one tap on touch screens), or Start → type → Enter |
| Start menu and search | Start button, **Ctrl+Space**, or `/` on the desktop. Recently used apps are listed first |
| Help | **?** in the taskbar or **F1**: getting around, shortcuts, what to do when something is wrong, *Copy details for support* and an email button (`SUPPORT_EMAIL`) |
| Half screen / maximise | Drag a window to the left or right edge / to the top; double-click the title bar |
| Switch / minimise | Click its taskbar button (again to minimise); middle-click closes; **Ctrl+`** cycles windows |
| Keyboard windows | Tab to a title bar: arrows move, Shift+arrows resize, Enter maximises, M minimises, Delete closes |
| Right-click / press and hold | On an icon, Start entry or taskbar button: open, open in a tab, pin to the taskbar, what the app is |
| Pop out | ↗ in the title bar opens the app in a normal browser tab |
| Phone | Every app fills the screen; the **back button closes it** |
| Install as an app | **Install** in the taskbar, or Settings → Install as an app (Chrome, Edge, Brave). Launcher shortcuts open This week, Photos, Recipes and Documents directly (`/?open=<id>`) |
| Settings | Theme (match device / dark / light), wallpaper, text and icon size, widgets on/off, reopen windows next time, tidy / close all, show the welcome tips again |

The first visit shows three welcome tips. Open windows, pins and preferences are remembered per person, per browser
(localStorage), and reopen on the next visit. Sign out goes through the outpost (`/outpost.goauthentik.io/sign_out`),
which ends the Authentik session for every app.

### What people see when things go wrong

- **"You've been signed out"** (full-screen): the Authentik session expired, noticed on a timer, when the tab comes
  back, or when a request fails. *Sign in again* reloads; windows come back.
- **"Can't reach home right now"**: the home server or the connection is down. The desktop retries by itself; a status
  line above the taskbar says when it is back.
- **A window stuck on "Still loading…"** after 15 s offers *Open in a browser tab* and *Try again*.
- **Widgets** say "not answering right now" for a source that is down, instead of vanishing.
- Icons whose image is missing show a lettered tile.

## Looking after it

Admins (`DASH_ADMIN_GROUP`, default `authentik Admins`) get a **System** app: version and uptime, a checklist with a fix
hint for each unmet requirement, each data source's last success / last error / timing, catalogue (`apps.json`)
warnings, the last 30 errors, and *Fetch fresh data now*. The same information is `GET /api/diagnostics`. The
container log is one timestamped, levelled line per event (`DASH_LOG_LEVEL`). The catalogue can be a mounted file
(`APPS_FILE`) so apps can be added without rebuilding; it is validated on every read and a bad entry is skipped,
never fatal. Settings, the catalogue format, deploy/rollback and a symptom → fix table are in
[`apps/dash/README.md`](../apps/dash/README.md); `npm test` covers the server.

## Limits

- Cross-origin windows can't be scripted, so a window has no back button of its own (on phones the browser's back
  button closes the window). Use the app's own navigation, or ⟳ to reload it.
- Passkeys inside a window need the browser to honour the iframe's `allow="publickey-credentials-get"`. Chrome and
  Firefox do. If a sign-in prompt misbehaves, use ↗. Adding a passkey happens in My account, which opens in a tab.
- `week.html` (the old one-page dashboard) is the **This week** app. Its "open ↗" links ask the desktop, via
  `postMessage`, to open the matching app window.
