#!/usr/bin/env bash
# 70-backup.sh — run ON THE HOST as root. Nightly restic backup of everything that matters, with retention.
#   70-backup.sh install            set up restic, repository, systemd timer (02:30 daily)
#   70-backup.sh run                back up now
#   70-backup.sh list|check|restore <snapshot> <target-dir>
# Repository: RESTIC_REPOSITORY in the plan file (a local path such as /mnt/backup/heardos, an sftp:user@host:/path,
# or an S3 URL); the repository password is generated into secrets.env. Media (photos, music) is included by default;
# set BACKUP_INCLUDE_MEDIA=false in the plan file to leave it out.
# The repository password lives in secrets.env, which is itself inside the backup: keep a copy OFF this host (the
# installer stores it in its vault from the HEARDOS_OUTPUT line `install` prints), or a dead disk means no restore.
. "$(dirname "$0")/lib/common.sh"; require_root; load_env
umask 077
: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY in the plan file (e.g. /mnt/backup/heardos)}"
export RESTIC_REPOSITORY; export RESTIC_PASSWORD; RESTIC_PASSWORD="$(secret RESTIC_PASSWORD 40)"
paths=("$HEARDOS_ROOT/data" "$HEARDOS_ROOT/stacks" "$HEARDOS_ROOT/secrets" "$HEARDOS_ROOT/dynamic" "$HEARDOS_ROOT/feeds" "$ENV_FILE")
[ "${BACKUP_INCLUDE_MEDIA:-true}" = true ] && paths+=("$HEARDOS_ROOT/media")
DUMPS="$HEARDOS_ROOT/secrets/dumps"   # database dumps: under secrets/ (0700), never next to app data

# dump_pg NAME CONTAINER-REGEX — pg_dumpall of a running Postgres container into $dir/NAME.sql (missing container = skipped)
dump_pg() {
  local c; c="$(docker ps --format '{{.Names}}' | grep -E "$2" | head -1 || true)"
  [ -n "$c" ] || return 0
  if docker exec "$c" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' > "$dir/$1.sql" 2> "$dir/$1.err"; then rm -f "$dir/$1.err"
  else warn "database dump FAILED for $1 ($c): $(tail -c 300 "$dir/$1.err")"; failed=1; fi
}
# dump_maria NAME CONTAINER — mariadb-dump using the root password from the container's own environment (not argv here)
dump_maria() {
  docker ps --format '{{.Names}}' | grep -qx "$2" || return 0
  if docker exec "$2" sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb-dump -uroot --all-databases --single-transaction' > "$dir/$1.sql" 2> "$dir/$1.err"; then rm -f "$dir/$1.err"
  else warn "database dump FAILED for $1 ($2): $(tail -c 300 "$dir/$1.err")"; failed=1; fi
}

case "${1:-}" in
  install)
    command -v restic >/dev/null || { apt-get -qq update && apt-get -qq install -y restic >/dev/null; }
    case "$RESTIC_REPOSITORY" in /*) install -d -m 700 "$RESTIC_REPOSITORY";; esac
    restic snapshots >/dev/null 2>&1 || restic init
    install -d -m 700 "$DUMPS"
    cat > /etc/systemd/system/heardos-backup.service <<EOF
[Unit]
Description=HeardOS restic backup
[Service]
Type=oneshot
UMask=0077
ExecStart=/bin/bash $HERE/70-backup.sh run
EOF
    printf '[Unit]\nDescription=Nightly HeardOS backup\n[Timer]\nOnCalendar=*-*-* 02:30:00\nRandomizedDelaySec=15min\nPersistent=true\n[Install]\nWantedBy=timers.target\n' > /etc/systemd/system/heardos-backup.timer
    systemctl daemon-reload; systemctl enable --now heardos-backup.timer >/dev/null
    out RESTIC_PASSWORD "$RESTIC_PASSWORD"   # once, for the installer's vault (masked in its log)
    msg "IMPORTANT: copy the backup password OFF this host now (password manager / the installer's vault):"
    msg "  grep '^RESTIC_PASSWORD=' $HEARDOS_ROOT/secrets/secrets.env"
    msg "  It is stored on this host and inside the encrypted backup only — lose the host and without a copy nothing can be restored."
    msg "installed. first run now:"; "$0" run ;;
  run)
    # consistent-enough database snapshots for a home system: one dump per database container, taken just before restic
    install -d -m 700 "$DUMPS"; dir="$(mktemp -d "$DUMPS/run.XXXXXX")"; failed=0
    dump_pg authentik '^authentik[-_](db|postgresql)[-_]1$'
    dump_pg paperless '^paperless[-_]db[-_]1$'
    dump_pg immich '^immich[-_]database[-_]1$'
    dump_pg nextcloud '^nextcloud[-_]db[-_]1$'
    dump_pg invidious '^invidious-db$'
    dump_pg listmonk '^listmonk-db$'
    dump_maria hortusfox hortusfox-db
    restic backup --tag heardos --exclude '*/model-cache' --exclude '*/tmp' --exclude '*/cache' "${paths[@]}"
    rm -rf "$dir"; find "$DUMPS" -mindepth 1 -maxdepth 1 -name 'run.*' -mmin +1440 -exec rm -rf {} +   # dumps live only in the repo
    restic forget --tag heardos --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
    restic snapshots --latest 1 --tag heardos
    [ "$failed" = 0 ] || die "backup finished, but at least one database dump failed (see above) — that database is NOT in this snapshot" ;;
  list) restic snapshots ;;
  check) restic check ;;
  restore)
    snap="${2:?snapshot id or latest}"; target="${3:?target dir}"
    case "$(realpath -m "$target")" in /|"$HEARDOS_ROOT"|"$HEARDOS_ROOT"/*) die "restore into an empty scratch dir (not / or $HEARDOS_ROOT), then copy back what you need";; esac
    # pre-check: the repository opens with this password, the snapshot exists, and the repository structure is sound
    restic cat config >/dev/null || die "cannot open $RESTIC_REPOSITORY with RESTIC_PASSWORD from secrets.env (wrong password? use your off-host copy)"
    restic snapshots "$snap" >/dev/null 2>&1 || [ "$snap" = latest ] || die "no snapshot $snap (70-backup.sh list)"
    restic check || die "repository check failed — do not trust a restore from it; try 'restic check --read-data' and 'restic repair'"
    restic restore "$snap" --target "$target" --verify
    msg "restored to $target; database dumps are in $target$DUMPS/run.*/ (pg: psql -f; mariadb: mariadb <)" ;;
  *) die "usage: $0 install|run|list|check|restore <snapshot> <dir>" ;;
esac
