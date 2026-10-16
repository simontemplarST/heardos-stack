#!/usr/bin/env python3
"""Generates the HeardOS network map: two SVG figures (request path, data flows), a combined poster SVG for print/PDF,
and the interactive HTML page. Every fact was checked against the live systems on 2026-10-14."""
import html, os

C = dict(paper="#F7F6F2", ink="#262628", muted="#5F5E59", line="#C9C6BC", amber="#f2a35e", amberink="#8a4a00",
         overlay="#B8781A", auth="#7A4FB2", data="#3C6E8F", public="#B23B3B", zoneA="#E6E4DD", zoneB="#E4E9E0", zoneC="#EEF0E8", band="#FFF7E6")
FONT = "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', 'DejaVu Sans Mono', Menlo, Consolas, monospace"
STYLE = f"""
  .zl {{ font: 600 11px {FONT}; letter-spacing: .08em; text-transform: uppercase; fill: {C['muted']}; }}
  .zone {{ fill: {C['zoneA']}; stroke: {C['line']}; }} .zone.b {{ fill: {C['zoneB']}; }} .zone.c {{ fill: {C['zoneC']}; }}
  .zone.ov {{ fill: {C['band']}; stroke: {C['overlay']}; stroke-dasharray: 8 5; stroke-width: 1.6; }}
  .node rect {{ fill: {C['paper']}; stroke: {C['ink']}; stroke-width: 1.2; }}
  .node.proxy rect {{ stroke: {C['auth']}; stroke-dasharray: 5 3; }}
  .node.public rect {{ stroke: {C['public']}; }}
  .node.authn rect {{ stroke: {C['auth']}; stroke-width: 1.8; }}
  .node.ovn rect {{ stroke: {C['overlay']}; }}
  .node text {{ font: 600 13px {FONT}; fill: {C['ink']}; }}
  .node .m {{ font: 400 10.5px {MONO}; fill: {C['muted']}; }}
  .node {{ cursor: pointer; }} .node:focus {{ outline: none; }}
  .node:focus rect, .node.on rect {{ stroke: {C['amber']}; stroke-width: 3; stroke-dasharray: none; }}
  .e {{ fill: none; stroke: {C['ink']}; stroke-width: 1.5; marker-end: url(#arr); }}
  .e.ov {{ stroke: {C['overlay']}; stroke-dasharray: 7 4; stroke-width: 2; marker-end: url(#arr-ov); }}
  .e.auth {{ stroke: {C['auth']}; marker-end: url(#arr-auth); }}
  .e.data {{ stroke: {C['data']}; stroke-width: 1.7; marker-end: url(#arr-data); }}
  .e.lan {{ stroke: {C['ink']}; stroke-dasharray: 2 3; marker-end: url(#arr); }}
  .el {{ font: 400 11px {FONT}; fill: {C['muted']}; }}
  .el.ov {{ fill: {C['overlay']}; }} .el.auth {{ fill: {C['auth']}; }} .el.data {{ fill: {C['data']}; }}
  .step circle {{ fill: {C['ink']}; }} .step text {{ font: 800 11px {FONT}; fill: {C['paper']}; }}
  .note {{ font: 400 10.5px {MONO}; fill: {C['muted']}; }}
  .h {{ font: 800 20px {FONT}; fill: {C['ink']}; }} .p {{ font: 400 12.5px {FONT}; fill: {C['muted']}; }}
  .th {{ font: 600 11px {FONT}; letter-spacing: .05em; text-transform: uppercase; fill: {C['muted']}; }}
  .td {{ font: 400 12.5px {FONT}; fill: {C['ink']}; }} .td.m {{ font: 400 11.5px {MONO}; }}
  .td.rw {{ fill: {C['amberink']}; font-weight: 600; }} .td.ro {{ fill: {C['muted']}; }}
  svg.dim .e:not(.hi), svg.dim .el:not(.hi), svg.dim .step:not(.hi) {{ opacity: .12; }}
  svg.dim .node:not(.hi):not(.on) {{ opacity: .3; }}
"""
DEFS = f"""<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{C['ink']}"/></marker>
  <marker id="arr-ov" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{C['overlay']}"/></marker>
  <marker id="arr-auth" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{C['auth']}"/></marker>
  <marker id="arr-data" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{C['data']}"/></marker>
</defs>"""

def esc(s): return html.escape(s, quote=True)

class Fig:
    def __init__(self, w, h, label):
        self.w, self.h, self.label, self.parts = w, h, label, []
    def add(self, s): self.parts.append(s)
    def zone(self, x, y, w, h, cls="", label=None, color=None, lx=14):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" class="zone {cls}"/>')
        if label: self.add(f'<text x="{x+lx}" y="{y+18}" class="zl"{" style=\"fill:%s\"" % color if color else ""}>{esc(label)}</text>')
    def node(self, x, y, w, h, title, sub=None, cls="", links="", desc="", dtitle=None, sub2=None):
        a = f' data-links="{links}" data-title="{esc(dtitle or title)}" data-desc="{esc(desc)}"' if desc else ''
        t = f'<g class="node {cls}" tabindex="0"{a}><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3"/>'
        t += f'<text x="{x+10}" y="{y+19}">{esc(title)}</text>'
        if sub: t += f'<text x="{x+10}" y="{y+36}" class="m">{esc(sub)}</text>'
        if sub2: t += f'<text x="{x+10}" y="{y+51}" class="m">{esc(sub2)}</text>'
        self.add(t + '</g>')
    def note(self, x, y, s): self.add(f'<text x="{x}" y="{y}" class="note">{esc(s)}</text>')
    def edge(self, id, d, cls="", label=None, lx=None, ly=None, step=None, rot=None, anchor=None):
        self.add(f'<path id="{id}" class="e {cls}" d="{d}"/>')
        if step: self.add(f'<g class="step"><circle cx="{lx-14}" cy="{ly-4}" r="8"/><text x="{lx-14}" y="{ly}" text-anchor="middle">{step}</text></g>')
        if label:
            tr = f' transform="rotate({rot} {lx} {ly})"' if rot else ''
            ta = f' text-anchor="{anchor}"' if anchor else ''
            self.add(f'<text class="el {cls.split()[0] if cls else ""}" x="{lx}" y="{ly}"{tr}{ta}>{esc(label)}</text>')
    def svg(self, standalone=False, cls="map"):
        head = ('<svg xmlns="http://www.w3.org/2000/svg" ' if standalone else '<svg ') + f'class="{cls}" viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.label)}">'
        style = f'<style>{STYLE}</style>' if standalone else ''
        bg = f'<rect width="{self.w}" height="{self.h}" fill="{C["paper"]}"/>' if standalone else ''
        return head + style + DEFS + bg + "".join(self.parts) + '</svg>'

# =============================== FIGURE 1: request path ===============================
f1 = Fig(1700, 920, "Request path: devices resolve example.com names at Cloudflare DNS, connect to Caddy on the Region-1 VPS, which forwards through the Nebula tunnel to Traefik inside LXC 108 on the home Proxmox host; Traefik asks Authentik, then routes to the app. Devices at home use sslip.io names, or host ports that bypass Traefik.")
f1.add('<text x="40" y="30" class="zl">Clients</text>')
f1.node(40, 42, 330, 56, "Family phones & laptops · anywhere", "cloud.example.com/<app> → 302 → <app>.example.com", links="e1 e-redir",
        desc="Open https://cloud.example.com/&lt;app&gt; from anywhere; Caddy answers with a 302 to the app's own host. The Homepage hub tiles and every Authentik launch URL use these cloud/&lt;slug&gt; links, so the family only ever types one address.")
f1.node(400, 42, 360, 56, "Native apps on the phones", "Immich · HA · Bitwarden · OPDS · Subsonic · DAV", links="e2b",
        desc="Immich, Home Assistant and Bitwarden apps log in through Authentik (OAuth / OIDC). KOReader (OPDS), Subsonic music players and CalDAV/CardDAV clients use per-app passwords on paths exempt from the SSO gate (/opds, /rest, /dav.php).")
f1.node(790, 42, 440, 56, "Devices at home · LAN 192.168.10.0/24", "<app>.192-168-10-20.sslip.io · or host ports on 192.168.10.20", links="e-home e-ports",
        desc="sslip.io names resolve to 192.168.10.20 without any DNS server and hit Traefik's LAN routers (plain http, or self-signed https). Seven apps also publish host ports that bypass Traefik and the SSO gate entirely: photos :2283, home :8123 (host network), mealie :9925, paperless :8012, homebox :7745, dav :6556, auth :9000 — plus the Runtipi dashboard on :80.")
f1.node(1260, 42, 400, 56, "Alice's laptop", "Nebula 10.42.0.20 · LAN route via prox · ssh · tipi.py · kit", links="e-lap e-route",
        desc="A Nebula node. From any network it reaches the lighthouse, CT 108 (10.42.0.11) and, through an unsafe_route via prox (10.42.0.10), the whole home LAN. The Nebula CA and the rebuild kit (~/heardos-install: scripts, compose overrides, Caddyfile copy) live here; secrets in ~/.config/credentials.env.")
f1.add('<text x="40" y="150" class="zl">Internet edge</text>')
f1.node(40, 162, 330, 72, "Cloudflare DNS · example.com", "20 × A  <host> → 203.0.113.10  (DNS only, unproxied)", links="e1 e2", sub2="apex + www → GitHub Pages · stale: mm, dco, forge",
        desc="DNS only, nothing proxied: auth books budget cloud cron dash dav docs home homebox hub mealie music office paperless photos speed tube vault wallos. The apex and www point at a GitHub Pages site. mm (old Mattermost), dco and forge are leftovers pointing elsewhere. Records are managed with cfdns-add / cfdns-del on CT 108.")
f1.zone(400, 150, 1260, 96, label="VPS \"nebula-lh\" · 203.0.113.10 · Region-1 · the only public address · hourly billed")
f1.node(420, 178, 500, 56, "Caddy 2.11 · public HTTPS ingress", "20 hosts, Let's Encrypt · → 10.42.0.11:80 · cloud = 302 redirector", links="e2 e2b e3 e-redir",
        desc="Terminates TLS for every host, sets X-Forwarded-Proto https / Port 443, and reverse-proxies to 10.42.0.11:80 through the Nebula tunnel. No upload size cap (150 MB verified). cloud.example.com maps /&lt;slug&gt;/path → https://&lt;host&gt;.example.com/path for 22 slugs, including proxmox → https://prox.lan:8006 and runtipi → http://192.168.10.20 (LAN-only targets); unknown slugs go to the Authentik app library. Logs in journald.")
f1.node(1100, 178, 540, 56, "Nebula lighthouse + relay · 10.42.0.1", "UDP 4242 · relays when NAT blocks a direct path · ssh only from group admins", cls="ovn", links="e-lap e3 e-lh",
        desc="Overlay 'example' 10.42.0.0/24. Nodes register here and traffic relays through it when a direct UDP path fails. Public port 22 is closed; SSH only over the overlay from certificates in group 'admins'.")
f1.zone(40, 296, 1620, 46, "ov", "Nebula overlay 10.42.0.0/24 · encrypted node-to-node · .1 lighthouse · .10 prox · .11 CT 108 · .20 laptop", C["overlay"])
f1.add('<text x="54" y="332" class="el ov">every node holds a certificate signed by the CA on the laptop; firewall rules are per certificate group (admins, servers, lighthouse)</text>')
f1.zone(40, 390, 1620, 510, "b", "Home LAN 192.168.10.0/24")
f1.note(470, 439, "PVE 9.2 · guests: LXC 108 + template 9000 · Zigbee dongle → CT 108 · web UI logs in with Authentik")
f1.node(1200, 414, 446, 40, "Proxmox \"prox\" · 192.168.10.2 · 10.42.0.10", links="e-route",
        desc="Proxmox VE 9.2. Guests: LXC 108 (the only one running) and a Debian 12 cloud-init template (9000). Its Nebula firewall lets group 'admins' reach local_cidr 192.168.10.0/24 and a NAT unit masquerades that traffic, so the laptop sees the whole LAN and the CT sees overlay clients as 192.168.10.2. The Sonoff Zigbee dongle (ZBDongle-E) is passed through to CT 108 by its /dev/serial/by-id path. The web UI (prox.lan:8006) logs in with Authentik (OpenID realm, app 'Proxmox VE', alice only).", dtitle="Proxmox VE 9.2 'prox' · 192.168.10.2 · overlay 10.42.0.10")
f1.zone(54, 472, 1592, 414, "c", "LXC 108 \"runtipi\" · 192.168.10.20 · overlay 10.42.0.11 · Debian 12 · Runtipi v4 on Docker · 8 cores / 96 GB", lx=416)
f1.node(68, 500, 1564, 40, "Traefik · :80", links="e3 e-home e4 e5 e6 e7",
        desc="Runtipi's reverse proxy. Public routers (file provider, containers by name — never the Docker provider, which hides a router while its container is 'starting') receive Caddy's traffic; the CT's Nebula firewall allows tcp/80 only from the lighthouse group. LAN routers serve &lt;app&gt;.192-168-10-20.sslip.io; on Books and Music they strip every X-authentik-* header so nobody on the LAN can forge a login. Home Assistant is reached at 192.168.10.20:8123 because it runs on the host network.")
f1.note(190, 525, "public routers pub-<host> from Caddy (tcp/80 allowed only from the lighthouse) · LAN routers <app>.192-168-10-20.sslip.io · auth headers stripped on books & music")
f1.node(68, 568, 1340, 40, "Authentik SSO · auth.example.com", cls="authn", links="e4 e6",
        desc="Forward auth for the gated apps (embedded outpost; Traefik asks before every request and passes X-authentik-username) and OIDC provider for the self-login apps and Proxmox. Users: alice, bob (akadmin is the break-glass superuser). Access to each app = membership of its app-&lt;slug&gt; group; app-proxmox is alice only. Launch URLs point at cloud.example.com/&lt;slug&gt;. Brand 'HeardOS'.")
f1.note(330, 593, "forward auth for gated apps · OIDC for the rest and for Proxmox · users alice, bob · one app-<slug> group per app · launch URLs → cloud/<slug>")
f1.add(f'<text x="68" y="654" class="zl" style="fill:{C["auth"]}">Gated · Traefik asks Authentik first, then forwards with the username header</text>')
gated = [("Hub", "hub", "Hub — Homepage dashboard", "Plain-word tiles (Photos, Recipes & meal plan, Documents, Music, Videos, Passwords, Home controls, Paperwork, Where is it?, Subscriptions, My account, Books) linking to cloud.example.com/&lt;slug&gt;. Budget, cron, office, Runtipi and Proxmox are deliberately not on it."),
         ("Books", "books", "Books — Calibre-Web", "Ebook manager (22 cookbooks, public 'Cookbooks' shelf). Logs you in from the Authentik header, trusted only from Traefik's network; /opds and /kobo are exempt so reader apps use a Calibre-Web password. Library: media/data/books."),
         ("Music", "music", "Music — Navidrome", "Streams media/data/music (read-only mount), rescans every 15 min. Header login; /rest, /share and /backgrounds are exempt for Subsonic players, which need a Navidrome password."),
         ("Videos", "tube", "Videos — Invidious", "YouTube front end with no login of its own, fully behind forward auth. No LAN route (https-only mode)."),
         ("Cron", "cron", "Cron — CronMaster", "UI over root's crontab on the CT host; gate = forward auth, plus its own OIDC login. Jobs today: five ics-feed-* calendar imports (every 15 min), actual-to-wallos (06:10 daily), artcraft-repository mirror (every 2 h)."),
         ("Dash", "dash", "Dash — heardos-dash (custom image)", "One-page family overview: this week's meals, open shopping lists, inventory, and the budget month once Actual's API password is enabled. Reads the Mealie, Homebox and Actual APIs, cached 3 min. No LAN route."),
         ("Calendar", "dav", "Calendar & contacts — Baïkal", "CalDAV/CardDAV. The admin UI is gated; /dav.php, /card.php, /cal.php and /.well-known are exempt and use Basic auth (Apple Calendar/Reminders, DAVx⁵, Home Assistant). Calendars: alice, bob, FamCal, Family Reminders, Meals, the public-feed calendars, and the Outlook work calendar imported into alice's own. Also on LAN port 6556."),
         ("Office", "office", "Office — OnlyOffice Document Server", "Custom app. Browsers load the editor and its WebSocket from here, so the host is gated by Authentik forward auth (since 2026-10-14, proxy provider onlyoffice, group app-onlyoffice); the Nextcloud embed passes silently while your Authentik session is valid. Nextcloud ↔ Document Server traffic stays on the Docker network (http://onlyoffice/, StorageUrl nextcloud_migrated-nextcloud-1) and documents are additionally protected by the shared JWT. LAN name office.192-168-10-20.sslip.io.")]
for i, (t, host, dt, d) in enumerate(gated):
    f1.node(68 + i * 150, 664, 138, 46, t, host, "proxy", "e5", d, dt)
f1.add(f'<text x="1440" y="654" class="zl" style="fill:{C["public"]}">No SSO</text>')
f1.node(1440, 664, 150, 46, "Speed", "speed", "public", "e7", "LibreSpeed, public on purpose and stripped to just the test. It measures client → Region-1 → tunnel → CT 108, and anyone can burn VPS bandwidth with it.", "Speed — LibreSpeed")
f1.add('<text x="68" y="754" class="zl">Self-login · Traefik passes them straight through; the app signs you in with Authentik (OIDC)</text>')
native = [("Photos", "photos", "Photos — Immich", "Photo library (16 GB in media/data/images/immich) with machine-learning, Postgres and Redis containers. Native OIDC including the mobile app's /api/oauth/mobile-redirect. Served via Caddy so uploads have no size cap. Also on LAN port 2283."),
          ("Docs", "docs", "Docs — Nextcloud 35 + OnlyOffice", "Family files, with OnlyOffice for in-browser documents (office.example.com, gated by Authentik + JWT). Mounts the other apps' folders as external storages — see table 3. LAN name nextcloud.192-168-10-20.sslip.io."),
          ("Recipes", "mealie", "Recipes — Mealie", "Recipes, meal plan and shopping lists. The meal plan becomes the 'Meals' calendar through a cron job; Home Assistant has a Mealie integration; the Dash reads the API. Also on LAN port 9925."),
          ("Paperwork", "paperless", "Paperwork — Paperless-ngx", "Scanned documents, local OCR, 16 category workflows; also holds the 22 cookbooks as type Book. Anything dropped into the consume folder (Nextcloud's 'Paperless inbox') is filed. Also on LAN port 8012."),
          ("Home", "home", "Home — Home Assistant", "Runs on the host network (port 8123). OIDC login via the hass-oidc-auth component. ZHA drives the Zigbee dongle (EmberZNet 8.0.3); CalDAV reads every Baïkal calendar and to-do list; plus Mealie, Apple TV, WiZ, Thread and mobile-app integrations."),
          ("Inventory", "homebox", "Inventory — Homebox", "Where things are. Registration is off; the Dash reads it through a dedicated service user's API key. Also on LAN port 7745."),
          ("Passwords", "vault", "Passwords — Vaultwarden", "Bitwarden-compatible vault, SSO only (password sign-up and sign-in disabled). Each user still sets a master password; the vault is end-to-end encrypted. Admin panel by token."),
          ("Subscriptions", "wallos", "Subscriptions — Wallos", "Recurring-cost tracker, filled every morning from Actual: monthly and quarterly payees in the Subscriptions and Bills & Utilities groups (10 today), priced at the latest charge, logos found online, stale ones marked inactive."),
          ("Budget", "budget", "Budget — Actual Budget", "Envelope budgeting with SimpleFIN bank sync. OpenID-only login (sessions never expire); automation uses a session token. The first OpenID login became the owner; bob auto-creates as a basic user.")]
for i, (t, host, dt, d) in enumerate(native):
    f1.node(68 + i * 174, 764, 162, 46, t, host, "", "e6", d, dt)
f1.node(68, 836, 1564, 40, "Runtipi dashboard · LAN only · http://192.168.10.20", links="e-ports",
        desc="Installs and updates the apps (store slug 'migrated' plus custom apps cronmaster, dash, onlyoffice). Not public: the cloud/runtipi slug redirects to the LAN address and the Authentik 'Runtipi' entry is a bookmark. Driven from the laptop with tipi.py against its API.")
f1.note(480, 861, "installs/updates the apps · custom apps cronmaster · dash · onlyoffice · shared folders under /root/runtipi/media and app-data (table 3)")
# edges (corridors: e3 x=440, e-home x=1080, e-route x=1365, e-ports right margin x=1652)
f1.edge("e1", "M205 98 V162", "", "resolve the name", 232, 134, step=1)
f1.edge("e-redir", "M330 98 C 330 125, 420 145, 445 178", "", "cloud/<app> → 302", 362, 120)
f1.edge("e2", "M370 198 H420", "", None, 395, 190, step=2)
f1.edge("e2b", "M580 98 V178", "", "HTTPS 443", 592, 140)
f1.edge("e-lap", "M1460 98 V178", "ov", "Nebula peer", 1472, 140)
f1.edge("e3", "M440 234 V500", "ov", "http :80 inside the tunnel to 10.42.0.11", 472, 270, step=3)
f1.edge("e-lh", "M1365 234 V296", "ov")
f1.edge("e-route", "M1365 342 V414", "ov", "laptop → LAN through prox (unsafe_route)", 1377, 384)
f1.edge("e-home", "M1080 98 V500", "lan", "at home: sslip.io names → Traefik", 1092, 268)
f1.edge("e-ports", "M1200 98 V120 H1652 V460 H1640 V472", "lan", "host ports go straight to the containers: no Traefik, no SSO", 1240, 114)
f1.edge("e4", "M120 540 V568", "auth", "forward-auth check per request", 176, 560, step=4)
f1.add('<path class="e auth" d="M140 568 V540"/>')
f1.edge("e5", "M300 608 V664", "auth", "allowed → app gets X-authentik-username", 332, 644, step=5)
f1.edge("e6", "M1600 540 V740 H1540 V764", "", "straight through · the app does OIDC with Authentik", 1530, 734, anchor="end")
f1.edge("e7", "M1515 540 V664", "", "no login", 1523, 630)

# =============================== FIGURE 2: data flows ===============================
f2 = Fig(1700, 400, "Data flows: public calendar feeds, the Mealie meal plan and Actual Budget feed CronMaster jobs, which write to Baïkal calendars and Wallos; Home Assistant reads Baïkal over CalDAV and the Zigbee radio; the Dash reads Mealie, Homebox and Actual; Nextcloud opens documents in OnlyOffice and drops files into Paperless.")
def n2(x, y, w, t, s, links, dt, d, cls=""): f2.node(x, y, w, 46, t, s, cls, links, d, dt)
n2(40, 190, 200, "Dash", "meals · lists · items", "g8 g9 g10", "Dash", "Reads the three APIs on each refresh (cached 3 min) and links every card back through cloud.example.com. The budget card waits on Actual's API password.", "proxy")
n2(300, 40, 260, "Public calendars", "county · city · seniors · work (ICS)", "g1", "Public calendars",
   "Example County and City of Springfield agendas, Smallville, the senior center, Discover Springfield, Historical Society, LocalVenue Historic Home, Riverland, Bruins, and the Outlook-published work calendar. The city's ICS export is broken server-side, so its agenda JSON is scraped instead.")
n2(300, 110, 260, "Recipes · Mealie", "meal plan · shopping lists", "g2 g8", "Mealie",
   "Meal plan entries become the 'Meals' calendar (one all-day event per dinner, linking to the recipe). The Dash and Home Assistant's Mealie integration read it directly.")
n2(300, 180, 260, "Budget · Actual", "transactions by payee", "g3 g9", "Actual Budget",
   "The actual-to-wallos job reads transactions with a never-expiring OpenID session token. The Dash's budget card still needs an API password (the server is OpenID-only).")
n2(300, 250, 260, "Inventory · Homebox", "items · locations", "g10", "Homebox", "Inventory totals, locations and recent items for the Dash, through a service user's API key.")
n2(300, 320, 260, "Docs · Nextcloud", "'Paperless inbox' · documents", "g12 g13", "Nextcloud",
   "The Paperless consume folder is mounted read-write as 'Paperless inbox': anything saved there is OCR'd and filed. Office documents open in OnlyOffice for editing.")
n2(700, 110, 300, "CronMaster jobs", "feeds every 15 min · actual→wallos 06:10", "g1 g2 g3 g4 g5", "CronMaster jobs",
   "Run as containers from root's crontab on the CT host. ics-feed-* (five jobs, one per feed): fetch, normalise, PUT one .ics per event into Baïkal, delete events that vanished upstream. actual-to-wallos: monthly/quarterly payees → Wallos subscriptions, idempotent via a marker in the notes. Logs appear in the CronMaster UI.", "proxy")
n2(1100, 40, 260, "Calendar · Baïkal", "feed calendars · Meals · work", "g4 g6", "Baïkal calendars",
   "One read-only calendar per feed shared to the family; the Meals calendar; the work calendar written into alice's own. FamCal and Family Reminders are co-owned by alice and bob.")
n2(1100, 150, 260, "Subscriptions · Wallos", "10 synced", "g5", "Wallos", "Ten subscriptions, matched by a marker in their notes so re-runs update instead of duplicating; stale payees are marked inactive, never deleted.")
n2(1100, 250, 260, "Office · OnlyOffice", "document server · JWT", "g13", "OnlyOffice Document Server", "Edits Nextcloud documents in the browser. The public host is gated by Authentik forward auth; Nextcloud and the Document Server talk over the Docker network with a shared JWT.", "proxy")
n2(1100, 320, 260, "Paperwork · Paperless", "consume → OCR → filed", "g12", "Paperless-ngx", "Watches the consume folder; local OCR; 16 category workflows tag and type each document.")
n2(1440, 40, 220, "Home Assistant", "calendars · to-dos · ZHA", "g6 g7", "Home Assistant",
   "CalDAV integration (read-only service user 'homeassistant') exposes every calendar and to-do list; a Mealie integration shows the plan; ZHA runs the Zigbee network on the passed-through dongle.")
n2(1440, 330, 220, "SSO · Authentik", "recovery · notifications", "g14", "Authentik", "Sends password-recovery links and admin notifications through Resend (smtp.resend.com:587, STARTTLS, send-only API key; DKIM signed with the apex domain, DMARC p=reject)", "authn")
n2(1440, 150, 220, "Zigbee radio", "Sonoff ZBDongle-E · 8.0.3", "g7", "Zigbee radio", "Passed from Proxmox into the CT by its stable /dev/serial/by-id path; EmberZNet firmware 8.0.3 since 2026-10-13.")
f2.edge("g8", "M300 133 H270 V200 H240", "data")
f2.edge("g9", "M300 203 H280 V213 H240", "data")
f2.edge("g10", "M300 273 H260 V226 H240", "data", "API reads", 248, 300, anchor="end")
f2.edge("g1", "M560 63 H630 V125 H700", "data", "fetch every 15 min", 568, 56)
f2.edge("g2", "M560 133 H700", "data", "meal plan", 568, 127)
f2.edge("g3", "M560 203 H630 V141 H700", "data", "transactions (API)", 568, 222)
f2.edge("g4", "M1000 125 H1050 V63 H1100", "data", "PUT events", 1008, 118)
f2.edge("g5", "M1000 141 H1050 V173 H1100", "data", "add · edit · inactive", 1008, 194)
f2.edge("g6", "M1360 63 H1440", "data", "CalDAV", 1368, 56)
f2.edge("g7", "M1550 150 V86", "data", "serial", 1558, 122)
f2.edge("g12", "M560 353 H1100", "data", "files dropped in 'Paperless inbox' (bind mount)", 640, 346)
f2.edge("g13", "M560 333 H1030 V273 H1100", "data", "open / save in OnlyOffice (JWT)", 640, 327)
f2.edge("g14", "M1550 330 V296", "data", "SMTP :25", 1558, 318)

# =============================== TABLE 3 (as SVG for the poster) ===============================
rows = [("media/data/books", "Calibre-Web  rw", "read · Books", "—"),
        ("media/data/music", "Navidrome  read", "rw · Music", "—"),
        ("media/data/images/immich", "Immich + machine learning  rw", "read · Immich photos", "—"),
        ("media/data/documents", "Nextcloud  rw", "rw · Family documents", "—"),
        ("media/data/{comics,movies,tv,podcasts,roms}", "none yet (empty)", "read", "—"),
        ("app-data/…/paperless_consume", "Paperless  rw (ingests, then deletes)", "rw · Paperless inbox", "—"),
        ("app-data/…/paperless_media/documents/originals", "Paperless  rw", "read · Paperless archive", "—"),
        ("app-data/…/paperless_export", "Paperless  rw", "read · Paperless export", "—"),
        ("app-data/…/mealie-1/data/recipes", "Mealie  rw", "read · Mealie recipes", "—")]
f3 = Fig(1700, 70 + 26 * len(rows) + 30, "Shared folders on LXC 108 and which apps mount them")
f3.add('<text x="40" y="30" class="zl">Shared folders on LXC 108 (bind mounts, read from the running containers) · databases, keys and configs are never shared</text>')
cols = [(40, "Folder on the CT"), (520, "Owner app"), (900, "Nextcloud external storage"), (1300, "Others")]
for x, h in cols: f3.add(f'<text x="{x}" y="56" class="th">{esc(h)}</text>')
for i, r in enumerate(rows):
    y = 82 + i * 26
    f3.add(f'<line x1="40" y1="{y+8}" x2="1660" y2="{y+8}" stroke="{C["line"]}"/>')
    for (x, _), v, cls in zip(cols, r, ("td m", "td", "td", "td")):
        c = cls + (" rw" if v.startswith("rw") else " ro" if v.startswith("read") else "")
        f3.add(f'<text x="{x}" y="{y}" class="{c}">{esc(v)}</text>')

# =============================== outputs ===============================
out = os.path.dirname(os.path.abspath(__file__))
gap = 70
poster_h = 90 + f1.h + gap + f2.h + gap + f3.h + 40
poster = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1700 {poster_h}" width="1700" height="{poster_h}">', f'<style>{STYLE}</style>', DEFS,
          f'<rect width="1700" height="{poster_h}" fill="{C["paper"]}"/>',
          '<text x="40" y="44" class="h">HeardOS Network Map</text>',
          '<text x="40" y="66" class="p">1 · Request path   2 · What feeds what   3 · Shared folders.   All public hosts are &lt;name&gt;.example.com; the drawings show only the name. Verified against the live systems on 14 Oct 2026.</text>',
          f'<g transform="translate(0,90)"><text x="40" y="-6" class="h" style="font-size:16px">1 · Request path</text>{"".join(f1.parts)}</g>',
          f'<g transform="translate(0,{90 + f1.h + gap})"><text x="40" y="-6" class="h" style="font-size:16px">2 · What feeds what</text>{"".join(f2.parts)}</g>',
          f'<g transform="translate(0,{90 + f1.h + gap + f2.h + gap})"><text x="40" y="-6" class="h" style="font-size:16px">3 · Shared folders</text>{"".join(f3.parts)}</g>',
          '</svg>']
open(os.path.join(out, "heardos-map.svg"), "w").write("".join(poster))
tpl = open(os.path.join(out, "template.html")).read()
page = tpl.replace("<!--FIG1-->", f1.svg()).replace("<!--FIG2-->", f2.svg()).replace("<!--STYLE-->", STYLE)
open(os.path.join(out, "index.html"), "w").write(page)
print("wrote heardos-map.svg (1700 x %d) and index.html" % poster_h)
