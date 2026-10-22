# Adopting a HeardOS that the installer did not build

A home server that was set up by hand, or by an older version of this kit, can be brought under the installer so the
Stacks screen, `heardos-cli`, the harness and `heardos-mcp` manage it. It happens in two parts that can be weeks apart:

1. **Adopt** records the install on the laptop and connects to the server. Nothing on the server changes except the
   installer's SSH key, a copy of the kit, an empty `$HEARDOS_ROOT` layout and the seeded `secrets.env`. From then on the
   read tools (status, logs, health, people) and start/stop/restart work on every stack.
2. **Migrate** moves one stack at a time into the kit layout. Each app is down for a few minutes; the rest keep running.
   After its move a stack is fully managed: install, settings, update, remove, "your own app" and the harness's
   change tools all work on it.

## Adopt

```bash
heardos-cli adopt my-install.json --vault vault.env --secrets secrets.env           # dry run: prints what would happen
heardos-cli adopt my-install.json --vault vault.env --secrets secrets.env --apply
```

The spec is JSON with the same plan keys an installer-made install has, plus how to reach the servers today:

```json
{"install_id": "smith", "ssh": {"host": "root@192.168.1.9", "vps": "lighthouse"},
 "plan": {"DOMAIN": "smithfamily.example", "ADMIN_USER": "alice", "ADMIN_EMAIL": "alice@example.net",
          "HOST_LAN_IP": "192.168.1.9", "HOST_SSH_FINGERPRINT": "SHA256:...", "HEARDOS_ROOT": "/opt/heardos",
          "provider": "byo", "byo_ip": "203.0.113.5", "NEBULA_LH_IP": "203.0.113.5", "NEBULA_LH_SSH": "10.42.0.1",
          "VPS_SSH_FINGERPRINT": "SHA256:...", "PORTAINER_URL": "https://192.168.1.9:9443", "AUTHENTIK_URL": "http://127.0.0.1:9000",
          "apps": ["immich", "mealie", "paperless"]}}
```

- `ssh` are the targets your own `ssh` already reaches; adopt uses them once to add the installer's key.
- The fingerprints pin the servers' SSH host keys without a prompt (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`).
- `--vault` holds the tokens the installer itself needs (Authentik automation token, Portainer access token, Cloudflare
  DNS token, mail API key); they go into the encrypted vault, never into the state file.
- `--secrets` holds the live secrets under the **kit's names** (`AUTHENTIK_<SLUG>_CLIENT_ID/SECRET`, `PAPERLESS_DB_PASSWORD`,
  `IMMICH_DB_PASSWORD`, `ONLYOFFICE_JWT_SECRET`, ...), so that when a stack migrates, `50-app.sh` reuses the existing
  sign-in registrations, database passwords and keys instead of generating new ones. Keys already present on the host win.

What adopt leaves behind: every install step is recorded as done ("adopted: already in place before the installer")
except the three it runs for real (installer key, first contact, kit upload); `plan.adopted_pending` lists the stacks
still in their old layout. For those the console refuses configure, update and remove with a message that names the
migrate command; restart, stop and start work (compose finds the containers by project label).

## Migrate

```bash
heardos-cli migrate smith                     # which stacks are pending, which are done
heardos-cli migrate smith mealie              # the plan and the preconditions, nothing changes
heardos-cli migrate smith mealie --apply
heardos-cli migrate smith mealie --rollback   # folders back, old stack redeployed from the spec's "old" files
```

The migration spec (`~/.config/heardos/migrate/<id>.json`, or `--spec`) says per stack where its data lives today:

```json
{"stacks": {
  "mealie":    {"moves": [["/srv/mealie/data", "data/mealie"]]},
  "nextcloud": {"moves": [["/srv/nextcloud/html", "data/nextcloud/html"], ["/srv/documents", "media/documents"]],
                "db": {"dump": {"container": "nextcloud-db", "user": "tipi", "db": "nextcloud"},
                       "restore": {"service": "db", "user": "nextcloud", "db": "nextcloud"}},
                "before": ["printf 'services:\n  nextcloud:\n    volumes: [/srv/books:/mnt/books]\n' > /opt/heardos/stacks/nextcloud/compose.override.yml"],
                "post_deploy": ["sed -i \"s/'dbhost' => '[^']*'/'dbhost' => 'db'/\" /opt/heardos/data/nextcloud/html/config/config.php"],
                "after": ["python3 /opt/heardos/migrate/drop-routers.py docs"]},
  "homeassistant": {"hold": "runs on the host network with a USB stick; needs a kit variant first"}}}
```

For one stack, in order: `before` hooks (as root on the server), stop the old containers, dump the database when the
kit runs another engine version (a dump and restore, never a binary copy across versions), rename each old folder to
its place under `$HEARDOS_ROOT` **leaving a symlink at the old path** (stacks not yet migrated that mount the same folder,
shared music, Paperless' inbox in Nextcloud, keep working even across restarts), run the install step for real
(`50-app.sh`: sign-in registration reused, `.env` from the seeded secrets, deployed through Portainer under the same
stack name so the old containers are replaced), `post_deploy` hooks, restore the dump, `after` hooks, then an HTTPS
check of the stack's public names. The stack leaves `adopted_pending`. Everything is recorded in the install's state
(`migrated.<stack>`), and `--rollback` renames the folders back and redeploys the old compose file.

Things the kit cannot know and the spec must say:

- **Database engine versions.** A cluster cannot move to an older major, and a Debian-built cluster must not move to
  an Alpine image of the same major (different collations corrupt text indexes). Same major and same libc: move the
  folder. Otherwise: `db` dump and restore. The kit's database images can be chosen per install (`AUTHENTIK_PG_IMAGE`,
  `PAPERLESS_PG_IMAGE`, `LISTMONK_PG_IMAGE`, `IMMICH_DB_IMAGE` in the plan) so an adopted install keeps what it runs.
- **Extra mounts and ports** the old stack had (external storages, a host-only port another app uses): a
  `compose.override.yml` written by a `before` hook. The kit keeps that file and merges it on every deploy.
- **Config files that name the old service hosts** (Nextcloud's `config.php`): a `post_deploy` hook.
- **Hand-written reverse-proxy routes** for the same host names: an `after` hook removes them once the kit's labels
  serve the host, or Traefik has two routers for one name.
- **A curated hub page**: a `# heardos: keep` line at the top of `services.yaml` stops `homepage-config.py` from
  regenerating it.

Core stacks (Traefik, Authentik) migrate the same way with `"step": "host.edge"` / `"host.authentik"`, but they take
the whole system down for a few minutes and usually need a network change first (the kit gives Traefik a fixed address
on the `proxy` network); keep them on `hold` until that is planned.

## Adding catalogue apps to an adopted install later

Apps that joined the catalogue after the adoption (or that you add to the plan) have install steps that never ran. Do
not run the whole DAG for them (`heardos-cli retry` on an adopted install therefore runs only the step it is given):
`50-app.sh` would deploy the kit's stack on empty folders while the old stack still holds the data. Add them to the plan
and to `adopted_pending`, give them spec entries, and `heardos-cli migrate` them like the others.
