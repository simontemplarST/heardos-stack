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

`apps.json` fields: `id`, `name`, `description`, `folder` (Start menu and desktop grouping), `icon` (a
[dashboard-icons](https://github.com/homarr-labs/dashboard-icons) name, downloaded into the image at build time by
`fetch-icons.mjs`, or `builtin:<name>`), `url` (`{domain}` substituted), `access`, optional `embed: false` (opens in a
browser tab, e.g. Proxmox on the home network only), and an optional default window size `w`/`h`.

## The edge has to allow framing by the desktop

Several apps forbid being framed at all: Authentik (`X-Frame-Options: DENY`), Homebox (`DENY`), and Nextcloud,
Paperless, Home Assistant and Vaultwarden (`SAMEORIGIN` / `frame-ancestors 'self'`). The lighthouse Caddy rewrites
that for every proxied host so that **only the family's own sites** (`<domain>` and `*.<domain>`) may frame it. That
covers the desktop and apps that embed each other, such as Nextcloud opening documents from OnlyOffice. The kit's
`generic/lib/caddyfile.py` renders it into every install:

```caddyfile
reverse_proxy <home-overlay-ip>:80 {
	# ...
	header_down -X-Frame-Options
	header_down Content-Security-Policy "frame-ancestors 'self'" "frame-ancestors 'self' https://example.com https://*.example.com"
	header_down Content-Security-Policy "frame-ancestors 'none'" "frame-ancestors https://example.com https://*.example.com"
	header_down +Content-Security-Policy "frame-ancestors 'self' https://example.com https://*.example.com chrome-extension: moz-extension: safari-web-extension:"
}
```

Clickjacking protection stays in place: `frame-ancestors` still blocks every site outside the family domain. It is
just widened from "nobody" or "only me" to "the family's sites". The extension schemes keep browser password-manager
extensions (e.g. Bitwarden's, already allowed by Vaultwarden) working. A list in the response may show the family
domain twice (the added policy is widened as well); that's harmless.

**Keep the quotes.** `'self'` and `'none'` must keep their single quotes. Unquoted, a browser reads `self` as a host
called "self", and the app can no longer frame itself. (The first live attempt lost them in shell quoting and broke
Nextcloud → OnlyOffice until it was fixed.)

## Using it

| Do | How |
|---|---|
| Open an app | Double-click its icon (one tap on touch screens), or Start → type → Enter |
| Start menu and search | Start button, **Ctrl+Space**, or `/` on the desktop |
| Half screen / maximise | Drag a window to the left or right edge / to the top; double-click the title bar |
| Switch / minimise | Click its taskbar button (again to minimise); middle-click closes |
| Pop out | ↗ in the title bar opens the app in a normal browser tab |
| Install as an app | **Install** in the taskbar, or Settings → Install as an app (Chrome, Edge, Brave). Launcher shortcuts open This week, Photos, Recipes and Documents directly (`/?open=<id>`) |
| Settings | Wallpaper, icon size, widgets on/off, reopen windows next time, tidy / close all |

Open windows are remembered per person, per browser (localStorage), and reopen on the next visit. Sign out goes
through the outpost (`/outpost.goauthentik.io/sign_out`), which ends the Authentik session for every app.

## Limits

- Cross-origin windows can't be scripted, so there's no back button. Use the app's own navigation, or ⟳ to reload it.
- Passkeys inside a window need the browser to honour the iframe's `allow="publickey-credentials-get"`. Chrome and
  Firefox do. If a sign-in prompt misbehaves, use ↗.
- `week.html` (the old one-page dashboard) is the **This week** app. Its "open ↗" links ask the desktop, via
  `postMessage`, to open the matching app window.
