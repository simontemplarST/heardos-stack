# Maintenance console (`tools`)

The desktop's **Maintenance** app: buttons for the maintenance and setup scripts, with live output and a history.
It is [OliveTin](https://olivetin.app) running natively on the apps host, configured by [`config.yaml`](config.yaml).
Nothing in the kit is rewritten for it: every button runs a script from CronMaster's library
(`/root/runtipi/app-data/_user/cronmaster/scripts`) or a short read-only command. The `config.yaml` here is the
generic copy (`example.com`, example feed and user names); edit those to match your own install.

| Piece | Where |
|---|---|
| `config.yaml` | `/etc/OliveTin/config.yaml` on the apps host: the buttons, their arguments, the dashboards, who may use it |
| `scripts/tools-run.sh` | Runs a script, shows its output live and keeps a copy under CronMaster's `data/logs/manual-<name>/`, framed like a scheduled run |
| `scripts/tools-status.sh` | The read-only reports (containers, disk, memory, certificates, jobs, county documents, Wikipedia, Nebula, journal, updates, Nextcloud) |
| Traefik router `pub-tools` | `Host(tools.<domain>)` → `http://172.30.0.1:1337`, middlewares `xfp-https, authentik-fwd` |
| Authentik | proxy provider + application `tools` (forward auth, single app); group `app-tools` = who sees the app on the desktop |
| Desktop | `apps/dash/apps.json` entry `tools` (folder *Admin*, access `tools`) |

## How access works

Traefik's Authentik forward auth signs people in and adds `X-authentik-username` / `X-authentik-groups` to the
request; OliveTin trusts those headers (`authHttpHeaderUsername`, `authHttpHeaderUserGroup`, separator `|`).
Two things keep that trust honest:

1. OliveTin listens **only** on `172.30.0.1:1337`, the host side of the private, internal docker network `tools`.
   Traefik is the only container on that network (`docker network connect tools traefik`, and the `tools` network in
   `stacks/traefik.yml`), so no other container can reach OliveTin and forge the headers.
2. `defaultPermissions` are all `false` and `authRequireGuestsToLogin: true`; the single ACL grants view/exec/logs to
   members of **`authentik Admins`** only. Someone in `app-tools` but not in that group sees an empty page.

## Adding a button

```yaml
  - title: Rebuild the thumbnails
    icon: "🖼️"
    shell: bash /root/runtipi/app-data/_user/cronmaster/scripts/tools-run.sh thumbs bash /root/runtipi/app-data/_user/cronmaster/scripts/thumbs.sh {{ size }}
    timeout: 1800
    arguments:
      - name: size
        title: Size
        default: small
        choices: [{ value: small }, { value: large }]
```

Then list the title under a dashboard. Rules of thumb:

- Wrap anything that changes state in `tools-run.sh <name> …` so the run is logged next to the scheduled ones.
- The default `timeout` is 3 s: set one for every action.
- Arguments are validated by type (`ascii_identifier`, `int`, `choices`, `confirmation`, `regex:…`). Free-form types
  (`url`, `password`, `regex:`) are refused with `shell:`; use `exec:` (an argv list) for those.
- Docker's own `{{.Names}}` format strings clash with OliveTin's templating: put them in a script (`tools-status.sh`).
- Destructive buttons get a `confirmation` argument.
- `systemctl restart OliveTin` after editing; `journalctl -u OliveTin` shows a config error on start.

## Installing

`heardos-install/tools/apply-maintenance.sh` in the operator's notes is the live version of these steps: install the
`.deb` from the OliveTin releases page, copy `config.yaml` and the two scripts, create the `tools` network and put
Traefik on it, add the router, the DNS name, the Authentik application (`42-ak-app.py proxy tools --name
Maintenance --host https://tools.<domain> --users <admin>`), the public host on the edge, and the desktop entry.

## When something is wrong

| Symptom | Usually | Do |
|---|---|---|
| "Unauthorized" / empty page | Headers not reaching OliveTin, or not an admin | Router must carry `authentik-fwd`; the person must be in `authentik Admins` |
| 502 from the desktop window | OliveTin down or Traefik not on the `tools` network | `systemctl status OliveTin`; `docker network inspect tools` should list `traefik` |
| A button fails at once with no output | Timeout (default 3 s) or a bad argument | Set `timeout`; check `journalctl -u OliveTin` |
| Config change not showing | OliveTin not restarted | `systemctl restart OliveTin` |
