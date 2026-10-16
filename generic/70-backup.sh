#!/usr/bin/env bash
# 70-backup.sh — run ON THE HOST as root. Nightly restic backup of everything that matters, with retention.
#   70-backup.sh install            set up restic, repository, systemd timer (02:30 daily)
#   70-backup.sh run                back up now
#   70-backup.sh list|check|restore <snapshot> <target-dir>
# Repository: RESTIC_REPOSITORY in the plan file (a local path such as /mnt/backup/heardos, an sftp:user@host:/path,
# or an S3 URL); the repository password is generated into secrets.env. Media (photos, music) is included by default;
# set BACKUP_INCLUDE_MEDIA=false in the plan file to leave it out.
. "$(dirname "$0")/lib/common.sh"; require_root; load_env
: "${RESTIC_REPOSITORY:?set RESTIC_REPOSITORY in the plan file (e.g. /mnt/backup/heardos)}"
export RESTIC_REPOSITORY; export RESTIC_PASSWORD; RESTIC_PASSWORD="$(secret RESTIC_PASSWORD 40)"
paths=("$HEARDOS_ROOT/data" "$HEARDOS_ROOT/stacks" "$HEARDOS_ROOT/secrets" "$HEARDOS_ROOT/dynamic" "$HEARDOS_ROOT/feeds" "$ENV_FILE")
[ "${BACKUP_INCLUDE_MEDIA:-true}" = true ] && paths+=("$HEARDOS_ROOT/media")
case "${1:-}" in
  install)
    command -v restic >/dev/null || { apt-get -qq update && apt-get -qq install -y restic >/dev/null; }
    case "$RESTIC_REPOSITORY" in /*) install -d "$RESTIC_REPOSITORY";; esac
    restic snapshots >/dev/null 2>&1 || restic init
    cat > /etc/systemd/system/heardos-backup.service <<EOF
[Unit]
Description=HeardOS restic backup
[Service]
Type=oneshot
ExecStart=/bin/bash $HERE/70-backup.sh run
EOF
    printf '[Unit]\nDescription=Nightly HeardOS backup\n[Timer]\nOnCalendar=*-*-* 02:30:00\nRandomizedDelaySec=15min\nPersistent=true\n[Install]\nWantedBy=timers.target\n' > /etc/systemd/system/heardos-backup.timer
    systemctl daemon-reload; systemctl enable --now heardos-backup.timer >/dev/null
    msg "installed. first run now:"; "$0" run ;;
  run)
    # quiesce databases cheaply: a consistent-enough snapshot for a home system is a pg_dump next to the data dir
    for s in authentik paperless; do
      dbc="$(docker ps --format '{{.Names}}' | grep -E "^${s}[-_](db|postgresql)[-_]1$" || true)"
      [ -n "$dbc" ] && docker exec "$dbc" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' > "$HEARDOS_ROOT/data/$s/pg_dumpall.sql" 2>/dev/null || true
    done
    [ -n "$(docker ps -q -f name=immich_database)" ] && docker exec "$(docker ps -q -f name=immich_database | head -1)" pg_dumpall -U postgres > "$HEARDOS_ROOT/data/immich/pg_dumpall.sql" 2>/dev/null || true
    restic backup --tag heardos --exclude '*/model-cache' --exclude '*/tmp' --exclude '*/cache' "${paths[@]}"
    restic forget --tag heardos --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
    restic snapshots --latest 1 --tag heardos ;;
  list) restic snapshots ;;
  check) restic check ;;
  restore) restic restore "${2:?snapshot id or latest}" --target "${3:?target dir}" ;;
  *) die "usage: $0 install|run|list|check|restore <snapshot> <dir>" ;;
esac
