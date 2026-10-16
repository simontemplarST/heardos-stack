# HeardOS: Runtipi → Portainer migration plan

Status: **plan, not started**. Written 2026-10-14 from a live inventory of LXC 108.
Source: `/root/runtipi` on CT 108 (Runtipi v4.10.2). Target: new LXC 109 running Docker + Portainer CE,
deployed with the kit in `~/heardos-install/generic` (stacks, Traefik, Authentik helpers).

## 1. Goals and non-goals

- Move all 19 apps off Runtipi onto plain Docker Compose stacks managed by Portainer, **with every byte of data intact**.
- Nothing outside the box changes: same LAN IP, same Nebula address, same hostnames, same Authentik apps and redirect URIs, same Caddy config on the VPS.
- Per-app downtime of minutes, never a whole-system outage; SSO interrupted once.
- Rollback at any time by starting CT 108 again.
- Not in scope: upgrading app versions during the move (pin what runs today, upgrade afterwards), changing the family-facing URLs.

## 2. Strategy in one paragraph

Build the new host **beside** the old one (CT 109), copy each app's bind-mounted data with that app stopped, start it on the new host, test it on a temporary name, and only at the end **swap identities**: CT 109 takes CT 108's MAC address (so DHCP hands it 192.168.10.20), its Nebula certificate/key (10.42.0.11, same firewall groups) and its Zigbee device passthrough. Keep the shared Docker network on the same subnet (172.18.0.0/16) so every "trusted proxy" setting keeps working. Databases are copied as files between identical Postgres majors, which is faster and more faithful than dump/restore; dumps are taken anyway as a second safety net.

## 3. Inventory (verified 2026-10-14)

### 3.1 Host and container

| Item | Value |
|---|---|
| CT 108 config | unprivileged, nesting+keyctl, 8 cores, 96 GB RAM, 4 GB swap, onboot, rootfs `Bittytb:vm-108-disk-0` 512 GB (74 GB used), `net0 hwaddr=BC:24:11:1D:11:8E ip=dhcp` |
| Device passthrough | `dev0: /dev/serial/by-id/usb-ITEAD_SONOFF_Zigbee_3.0_USB_Dongle_Plus_V2_20230605100332-if00,mode=0666` |
| Addresses | LAN 192.168.10.20 (DHCP by MAC), Nebula 10.42.0.11 (`/etc/nebula/` host.crt, host.key, config.yml; inbound tcp/80 from group `lighthouse`, any from `admins`/`servers`) |
| Runtipi networks | `runtipi_tipi_main_network` **172.18.0.0/16** (Traefik + every app), `runtipi_tipi_core_network`, five per-app networks (authentik, immich, invidious, nextcloud, paperless) |
| Runtipi services to retire | `runtipi` (dashboard, port 80 root), `runtipi-db` (its own Postgres), `runtipi-queue` (RabbitMQ), `runtipi-reverse-proxy` (Traefik 3.6.14) |
| Traefik config to carry | `/root/runtipi/traefik/traefik.yml`, `dynamic/dynamic.yml` (middlewares, HA service), `dynamic/local-sso.yml` (authentik-fwd, strip-authentik, noop, authentik-outpost service), `dynamic/public-example.yml` (20 public routers, 8 outpost routers, 11 file services by container name) |
| Root crontab | 7 jobs via `/root/runtipi/app-data/_user/cronmaster/data/cron-log-wrapper.sh`; scripts in `.../cronmaster/scripts/`; two jobs `docker run` the locally built image `heardos-dash:0.1` |
| Media | `/root/runtipi/media/data` 22 GB (books, music, images/immich 16 GB, documents, empty comics/movies/tv/podcasts/roms) |
| Backups today | nightly vzdump 03:00 → `entertainmentdata` (keep 7 daily / 4 weekly, ~46 GB each); manual vzdump 2026-10-14 → `Media` |

### 3.2 Per-app table

Paths are relative to `/root/runtipi/app-data/`. "Override" = our customisations in `/root/runtipi/user-config/<store>/<app>/docker-compose.yml`, already merged into `/root/runtipi/apps/<store>/<app>/docker-compose.generated.yml` (use that file as the source of truth for the new stack). "Secrets" = keys in `app.env` that must be copied verbatim (Runtipi generates them once; the databases and apps were initialised with them).

| App (Runtipi id) | Images (pinned today) | Data dirs | Secrets to carry | Override contents | Special handling |
|---|---|---|---|---|---|
| Authentik (`authentik-1`) | goauthentik/server:2026.8.3 (server+worker), postgres:18-alpine, redis | `migrated/authentik-1/data/{authentik-media,authentik-certs,authentik-custom-templates,postgres}` | `AUTHENTIK_SECRET_KEY`, `AUTHENTIK_DB_PASSWORD` | TRUSTED proxies / branding media mount | **Migrate first**; everything depends on it. Traefik `authentik-fwd` middleware and `authentik-outpost` service point at its container name. Embedded outpost `authentik_host` = `http://auth.192-168-10-20.sslip.io` (unchanged thanks to identity swap). |
| Paperless-ngx | paperless-ngx:3.3.0, postgres:17, redis:7, gotenberg:8.37, tika | `migrated/paperless-ngx/data/{paperless_data,paperless_media,paperless_consume,paperless_export,postgres,redis}` | `PAPERLESS_SECRET_KEY`, `PAPERLESS_ADMIN_*`, DB password (in generated compose) | OIDC env (`PAPERLESS_SOCIALACCOUNT_PROVIDERS` etc.), local OCR, `PAPERLESS_URL` | 16 workflows + Cookbook tag live in the DB. Consume/export/originals are mounted into Nextcloud too. |
| Nextcloud | nextcloud:35.0.1 (+ nextcloud:35 cron), postgres:14.2, redis:6.2.6 | `migrated/nextcloud/data/{nextcloud,db,redis}` | `NEXTCLOUD_ADMIN_*`, DB password; **also baked into `data/nextcloud/config/config.php`** (`dbhost` = old container name) | trusted domains/proxies, overwrite URLs, 16 GB upload, 13 extra bind mounts (books, music, immich, documents, paperless ×3, mealie recipes, comics/movies/tv/podcasts/roms), cron container with the same mounts | Edit `config.php` `dbhost`/`redis host` to the new service names, or keep the old container names in the new stack. OnlyOffice connector URLs: public `https://office.example.com/`, internal `http://onlyoffice/`, storage `http://nextcloud_migrated-nextcloud-1/` → update to new names. Local patch for app-store thumbnails must be re-applied (`nextcloud-appstore-screenshots-patch.py`). |
| Immich | immich-server:v3.3.1, immich-machine-learning:v3.3.1, immich postgres:14-vectorchord0.3.0-pgvectors0.2.0, redis:6.2 | `migrated/immich/data/{db,immich-ml-cache}` + `media/data/images/immich` (uploads) | `DB_PASSWORD`, `IMMICH_TYPESENSE_API_KEY` | CPU caps (server 4, ML 2) | 16 GB of uploads; ML cache is re-downloadable. Pin the exact vectorchord image. OIDC config is inside its DB (system config). |
| Home Assistant (`homeassistant-1`) | home-assistant:2026.9.3 | `migrated/homeassistant-1/data/config` (incl. `custom_components/auth_oidc`, `.storage`, ZHA backups) | none in app.env (OIDC client id/secret are in configuration.yaml) | `devices:` by-id path, host network | `network_mode: host`; needs `dev0` in the new pct config (same by-id path). Traefik service `homeassistant` → `http://192.168.10.20:8123`. |
| Mealie (`mealie-1`) | mealie:v3.28.0 | `migrated/mealie-1/data` (sqlite `mealie.db`, recipes, backups) | `MEALIE_ALLOW_SIGNUP` | OIDC env, base URL | API tokens (HA, dash, cron) are in the DB. |
| Homebox | homebox:0.26.2-rootless | `migrated/homebox/data/homebox-data` (sqlite) | `HBOX_AUTH_API_KEY_PEPPER` (API keys are hashed with it) | OIDC env, registration off | rootless image: files owned by uid 65532 — preserve. |
| Baïkal | aalmenar/baikal:0.12.1 | `migrated/baikal/{config,specific}` (sqlite `specific/db/db.sqlite`) | none | proxy awareness | uid 33 ownership. DAV clients use Basic auth → unaffected. |
| Navidrome | deluan/navidrome:0.64.2 | `migrated/navidrome/navidrome` + `media/data/music` (ro) | none | header login env, `ND_EXTAUTH_TRUSTEDSOURCES=172.18.0.0/16`, strip-authentik labels | subnet must stay 172.18.0.0/16 or update the env. |
| Calibre-Web | linuxserver/calibre-web:0.6.27 | `migrated/calibre-web/data/{config,calibre}` + `media/data/books` (library, metadata.db) | none | `DOCKER_MODS=linuxserver/mods:universal-calibre`, strip-authentik labels | trusted proxy list `172.18.0.0/16,::ffff:172.18.0.0/112` is in `config/app.db`. |
| Vaultwarden | vaultwarden/server:1.37.4 | `migrated/vaultwarden/data` (db.sqlite3, rsa_key.pem, attachments) | `VAULTWARDEN_ADMIN_PASSWORD`/token | SSO env (`SSO_*`, `DOMAIN`, signups off) | `rsa_key.pem` is the token signing key — must move with the DB. |
| Wallos | bellamy/wallos:5.8.3 | `migrated/wallos/data/{db,logos}` | none | `OIDC_*` env, TZ | uid 82 ownership. |
| Actual Budget | actual-server:26.10.0 | `migrated/actual-budget/data/{server-files,user-files}` | none in app.env (OpenID bootstrap is in `server-files/account.sqlite`) | `ACTUAL_OPENID_*`, login method, trusted proxies | |
| Invidious | invidious:master, invidious-companion:latest, postgres:14 | `migrated/invidious/data/{postgres,init,sql}` | `INVIDIOUS_HMAC_KEY`, `INVIDIOUS_COMPANION_KEY`, domain/port/https flags | none | `:master`/`:latest` tags: record the image digests before moving. |
| Homepage | gethomepage/homepage:v2.5.0 | `migrated/homepage/data` (YAML config) | none | `HOMEPAGE_ALLOWED_HOSTS`, PUID/PGID 1000 | config is plain files; trivial. |
| LibreSpeed | linuxserver/librespeed:6.3.0 | `migrated/librespeed/data` (custom index.html) | none | noop LAN route | public, no auth. |
| CronMaster (`cronmaster:_user`) | fccview/cronmaster:latest | `_user/cronmaster/{data,scripts,snippets}` **+ root's crontab on the host** | none | git in entrypoint | **Keep this app's path exactly** (`/root/runtipi/app-data/_user/cronmaster`) so the 7 crontab lines and the log wrapper keep working; copy crontab with `crontab -l` → `crontab -`. Jobs need `heardos-dash:0.1` rebuilt on the new host and the images `heardos-bw` (not needed any more) absent. |
| Dash (`dash:_user`) | heardos-dash:0.1 (local build from `~/heardos-install/dash`) | `_user/dash/data` (Actual cache) | secrets injected by `set-secrets.sh` (Mealie/Homebox tokens) | — | rebuild the image on 109 first (`docker build`), then re-run `set-secrets.sh` with the new API base. |
| OnlyOffice (`onlyoffice:_user`) | onlyoffice/documentserver:9.3 | `_user/onlyoffice/{data,db,lib,logs}` | JWT secret in the generated compose (also in Nextcloud's `onlyoffice` app config) | shared network with Nextcloud | now gated by Authentik forward auth (router + outpost router). |

Overrides exist for every app except Invidious and Dash; all 15 are captured in `~/heardos-install/overrides-20261012/` (copies) and in the generated compose files.

### 3.3 Things that reference container names or Runtipi paths (must be rewritten)

- `traefik/dynamic/public-example.yml`: services `onlyoffice__user-onlyoffice-1:80`, `cronmaster__user-cronmaster-1:3000`, `wallos_migrated-wallos-1:80`, `invidious_migrated-invidious-1:3000`, `vaultwarden_migrated-vaultwarden-1:80`, `actual-budget_migrated-actual-budget-1:5006`, `homepage_migrated-homepage-1:3000`, `calibre-web_migrated-calibre-web-1:8083`, `librespeed_migrated-librespeed-1:80`, `dash__user-dash-1:8080`, plus `@docker` services for auth, paperless, mealie, homebox, dav, music, photos, docs.
- `traefik/dynamic/local-sso.yml`: `authentik-fwd` address and `authentik-outpost` service → `authentik-1_migrated-authentik-1-1:9000`.
- Nextcloud `config.php` (`dbhost`, redis), Nextcloud OnlyOffice app settings (`DocumentServerInternalUrl`, `StorageUrl`).
- Paperless `PAPERLESS_DBHOST`/`PAPERLESS_REDIS`, Immich `DB_HOSTNAME`/`REDIS_HOSTNAME`, Authentik `AUTHENTIK_POSTGRESQL__HOST`/`AUTHENTIK_REDIS__HOST`, Invidious `config.yml` db host: all env in the generated compose; set to the new service names.
- Home Assistant `.storage/http` trusted proxies `172.16.0.0/12` (fine), `auth_oidc` discovery URL (public, fine).
- `~/heardos-install/dash/set-secrets.sh` (`RUNTIPI_BASE`, talks to the Runtipi API) → replace with a Portainer stack update or plain `docker compose`.
- Memory/kit scripts that call `tipi.py` (restart/install): retire.

## 4. Decisions to make before starting

| # | Decision | Recommendation |
|---|---|---|
| D1 | New CT 109 + identity swap vs in-place | **New CT.** In-place fights Runtipi's Traefik on :80, its networks and the dashboard; rollback would be the vzdump only. |
| D2 | Data layout on 109 | **Keep `/root/runtipi/app-data/...` and `/root/runtipi/media/...` paths verbatim** for this migration (zero path edits in crontab, scripts, Nextcloud mounts, HA device lines). Rename later if wanted. CronMaster in particular must keep its path. |
| D3 | Docker network | One external network `proxy` with **subnet 172.18.0.0/16, gateway 172.18.0.1** (recreating Runtipi's main network) + per-stack internal networks for DB/redis. |
| D4 | Portainer access | LAN + overlay only on :9443 (and `portainer.192-168-10-20.sslip.io`); OAuth login via a new Authentik OIDC app `portainer` (Portainer CE supports a custom OAuth provider). No public host. |
| D5 | LAN host ports that bypass SSO (photos 2283, home 8123, mealie 9925, paperless 8012, homebox 7745, dav 6556, auth 9000) | New stacks publish **none** except HA (host network, unavoidable) and Authentik :9000 (used by the kit scripts and HA's OIDC discovery). Re-add any you actually use. |
| D6 | Container names | Short fixed `container_name`s (`authentik`, `immich`, `nextcloud`, …) as in the kit; rewrite the two Traefik dynamic files once. |
| D7 | Image tags | Pin exactly what runs today (section 3.2); `invidious:master`/`companion:latest` pinned by digest. Upgrade after the move, one app at a time. |

## 5. Phases

### Phase 0 — Safety net (no downtime, ~1 h)
1. vzdump of CT 108 to `Media` (done 2026-10-14 20:06, suspend mode) and the nightly one on `entertainmentdata`.
2. On CT 108: `pg_dump` of the five databases into `/root/migration/dumps/` (authentik, paperless, nextcloud, invidious; immich with `pg_dumpall --clean` per Immich docs), plus `sqlite3 .backup` of mealie, homebox, baikal, vaultwarden, wallos, actual.
3. Archive the inputs: `tar` of `/root/runtipi/apps/*/*/docker-compose.generated.yml`, all `app.env`, `user-config/`, `traefik/`, root crontab, `/etc/nebula/`, `docker images --digests`. Copy to the laptop kit under `docs/migration-inputs-<date>/` (contains secrets → 0600, not in git).
4. Generate the Portainer `.env` files from the `app.env`s with a script (never retype a secret).

### Phase 1 — New host (no downtime, ~1 h)
1. `pct create 109` Debian 12, unprivileged, `features: nesting=1,keyctl=1`, 8 cores, 96 GB, rootfs on `Bittytb` (512 GB), temporary DHCP MAC, onboot 0 for now.
2. Run `10-host-bootstrap.sh` (Docker + compose plugin, Portainer CE, layout, `proxy` network with the 172.18.0.0/16 subnet, ufw, unattended upgrades, SSH key).
3. Temporary Nebula identity: sign `heardos-new` as 10.42.0.12 in groups `servers`; install `/etc/nebula`.
4. Create `/root/runtipi/app-data` and `/root/runtipi/media` on 109 (same paths, D2). Build `heardos-dash:0.1` from the kit.
5. Portainer: local admin created, API token stored in `credentials.env`, environment "local".

### Phase 2 — Edge (no downtime, ~1 h)
1. Traefik stack (kit `stacks/traefik`) on 109 with the three dynamic files adapted: middlewares unchanged; `authentik-fwd` and `authentik-outpost` → `authentik:9000`; file services → new container names; HA service → `http://192.168.10.20:8123` (correct after the swap; `http://<temp-ip>:8123` during testing).
2. Temporary LAN names `*.10-1-0-<temp>.sslip.io` for testing. Public hosts are **not** moved yet (Caddy still points at 108).

### Phase 3 — Authentik first (~10 min SSO outage, ~1 h)
1. Stop Authentik on 108 (`tipi.py stop authentik-1:migrated`).
2. `rsync -aHAX --numeric-ids --delete` `migrated/authentik-1/` → 109.
3. Start the Authentik stack on 109; wait for migrations; log in on the temp name; confirm the embedded outpost shows healthy and all 19 apps + 8 proxy providers are present.
4. Start Authentik on 108 again (both can run; they share nothing). Public SSO keeps using 108 until cutover. **Rule from here on: do not change Authentik objects on either side until cutover, or re-sync once more at cutover.**

### Phase 4 — Simple apps (minutes each, ~4 h total)
Order: LibreSpeed, Homepage, Invidious, Navidrome, Calibre-Web, Baïkal, Wallos, Actual, Vaultwarden, Homebox, Mealie, OnlyOffice, Dash, CronMaster.
Per app: stop on 108 → rsync data (`--numeric-ids`) → write the stack from `docker-compose.generated.yml` + `.env` → `portainer-stack.py` deploy → check logs for a clean start (no "initialising database", no auth errors) → open on the temp name → **start the app on 108 again** so the family keeps using it until cutover.
Checks that prove data arrived: Navidrome library count, Calibre-Web 22 books, Baïkal calendar list, Wallos 10 subscriptions, Actual budget opens with history, Vaultwarden prelogin + unlock, Homebox item count (13+), Mealie recipe count and meal plan, Homepage tiles, CronMaster shows 7 jobs with logs.

### Phase 5 — Heavy apps (~15 min each, ~3 h)
- Paperless: stop → rsync 1.7 GB → start DB first, check `pg_isready` and that it did **not** init a new cluster → start app → document count and a search.
- Nextcloud: stop → rsync 1.4 GB → fix `config.php` hosts → start → `occ status`, `occ files_external:list` (13), open a doc in OnlyOffice via the temp name.
- Immich: stop → rsync 2 GB DB + 16 GB uploads (first pass while running, final pass stopped) → start DB (vectorchord image) → server → asset count equals old; ML cache optional.
- Home Assistant: stop → rsync 11 MB → add `dev0` to CT 109 (`pct set 109 -dev0 ...`, needs a 109 reboot) → start host-network container → ZHA devices online, calendars load.

### Phase 6 — Burn-in (1–2 days, no downtime)
Everything runs on both. Verify 109 end to end on the temp names (checklist §7). Fix stack issues. Re-sync any app that was used meanwhile (all data edits still happen on 108, so a final delta sync at cutover is mandatory anyway).

### Phase 7 — Cutover (30–45 min downtime, morning)
1. Announce; stop all Runtipi apps on 108 (`tipi.py stop` each, or `docker stop $(docker ps -q)` after stopping the Runtipi service).
2. Final `rsync --delete` of **every** app-data dir and `media/data` to 109 (deltas only, minutes). Re-dump the five DBs for the record.
3. On prox: `pct shutdown 108`; `pct set 108 -onboot 0`.
4. Identity swap on 109: `pct set 109 -net0 name=eth0,bridge=vmbr0,hwaddr=BC:24:11:1D:11:8E,ip=dhcp,type=veth`, `-dev0 <same by-id path>`, `-onboot 1`; copy 108's `/etc/nebula/{host.crt,host.key,config.yml}` into 109 (replacing the temp identity); set hostname `runtipi` (or keep `heardos` and update Caddy's nothing — hostname is not referenced anywhere); `pct reboot 109`.
5. 109 comes up as 192.168.10.20 / 10.42.0.11. Caddy on the VPS needs **no change**. Confirm `curl` from the lighthouse to the public hosts; confirm sslip names.
6. Portainer: start all stacks (they are `restart: unless-stopped`; verify order: traefik, authentik, then the rest).
7. Run the checklist (§7). Family announcement: done.

### Phase 8 — Afterwards (~1 h)
- Point the nightly vzdump job (`/etc/pve/jobs.cfg`, vmid 108 → 109); run one manually.
- Authentik: replace the `runtipi` bookmark app with `portainer` (OIDC); update the cloud.example.com redirector slug `runtipi` → `portainer` target `https://192.168.10.20:9443`.
- Homepage/hub untouched. Update `~/heardos-install` scripts that call `tipi.py` (dash `set-secrets.sh`, memory notes).
- Keep CT 108 stopped for 14 days, then `pct destroy 108` and reclaim the 512 GB LV. Keep the Phase 0 vzdump on `Media` for 90 days.

## 6. Data-loss guards (the ones that matter)

| Risk | Guard |
|---|---|
| Copying a live database | Owner app always stopped before its rsync; cutover does a full stopped delta sync. |
| Wrong/missing secret → app silently initialises an empty DB beside the real one | `.env` generated from `app.env`; first start checks DB logs for "database system is ready" without "initdb", app logs without "password authentication failed"; row counts compared before declaring success. |
| Postgres major mismatch | Tags pinned from `docker ps` on 108; `docker image inspect` both sides before starting. |
| Ownership/ACL loss | `rsync -aHAX --numeric-ids`; spot checks: Immich 1000, Nextcloud 33, Wallos 82, Homebox 65532, Postgres 999/70. |
| Zigbee network loss | HA config dir carries the ZHA state; same by-id device path; ZHA backup JSON already in `/config`. |
| Cron jobs vanish | crontab copied verbatim (`crontab -l` on 108 → `crontab -` on 109); same scripts path; `heardos-dash:0.1` rebuilt before cutover. |
| Authentik drift between phases 3 and 7 | Freeze Authentik changes, or re-sync its data at cutover (it is stopped then anyway). |
| Caddy/DNS changes | None required; that is the point of the identity swap. |
| Rollback after cutover | Shut 109, move MAC/Nebula back, start 108; only data written on 109 since cutover would need a reverse rsync, hence a short morning window. |

## 7. Verification checklist (run on the temp names in phase 6, on the real names after cutover)

- [ ] Authentik: 19 applications, 8 proxy providers on the embedded outpost (healthy), users alice + bob, a gated app and an OIDC app both log in.
- [ ] Immich: asset count equals 108's (`SELECT count(*) FROM assets`), a photo opens, mobile app syncs via `photos.example.com` after cutover.
- [ ] Nextcloud: `occ status` installed, 13 external storages, file count in Family documents matches, OnlyOffice edit saves, app-store patch re-applied.
- [ ] Paperless: document count ≥ 22 cookbooks + prior docs, search works, a file dropped in the inbox is consumed.
- [ ] Baïkal: calendars unchanged; a feed job runs green in CronMaster; HA CalDAV entities populate.
- [ ] Wallos 10 subscriptions with logos; Actual opens the budget with history; Vaultwarden unlocks; Mealie meal plan present; Navidrome plays; Calibre-Web 22 books + Cookbooks shelf.
- [ ] Home Assistant: ZHA devices online, PAN/channel unchanged, OIDC login works, Mealie/CalDAV integrations loaded.
- [ ] Dash shows meals/lists/items; cron `actual-to-wallos` dry run passes.
- [ ] From the lighthouse: every public host answers; `speed.example.com` test runs; `cloud.example.com/<slug>` redirects.
- [ ] Nebula: `nebula-cert print` on 109 shows 10.42.0.11; laptop reaches 10.42.0.11 and the LAN route.
- [ ] Proxmox nightly job targets 109 and a manual run succeeds.
- [ ] No container publishes a LAN host port that wasn't decided in D5.

## 8. Effort

| Phase | Hands-on | Clock |
|---|---|---|
| 0 Safety net | 1 h | 1 h |
| 1 Host | 1 h | 1 h |
| 2 Edge | 1 h | 1 h |
| 3 Authentik | 1 h | 1 h |
| 4 Simple apps | 4 h | 1 day |
| 5 Heavy apps | 3 h | half a day |
| 6 Burn-in | 1 h | 1–2 days |
| 7 Cutover | 1 h | 1 h |
| 8 Afterwards | 1 h | 2 weeks (108 retention) |

Total hands-on: about 14 hours. Family-visible downtime: minutes per app during phases 3–5, plus one 30–45 minute window at cutover.
