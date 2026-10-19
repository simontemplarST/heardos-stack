#!/usr/bin/env bash
# 61-feeds-timer.sh — run ON THE HOST as root. Installs the calendar feed importer and a systemd timer (every 15 min).
# Config: $HEARDOS_ROOT/feeds/ics-feeds.json (created from the example on first run; edit it, then re-run to validate).
# The importer fetches arbitrary internet URLs, so it runs as its own system user (heardos-feeds), not root: it may write
# only its own folder and Baïkal's sqlite folder (as a member of www-data, Baïkal's file group), nothing else.
. "$(dirname "$0")/lib/common.sh"; require_root; load_env
U=heardos-feeds; BDB="$HEARDOS_ROOT/data/baikal/specific/db"
id "$U" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$U"
getent group www-data >/dev/null || groupadd -g 33 www-data
usermod -aG www-data "$U"
F="$HEARDOS_ROOT/feeds"; install -d -m 700 -o "$U" -g "$U" "$F" "$F/.ics-feeds-state"
install -m 500 -o "$U" -g "$U" "$HERE/ics-feeds-sync.py" "$F/ics-feeds-sync.py"
# sqlite needs write access to the db file and its folder (journal); Baïkal (uid/gid 33) keeps ownership
[ -d "$BDB" ] && chmod g+rwX "$BDB" "$BDB"/db.sqlite* 2>/dev/null || true
if [ ! -f "$F/ics-feeds.json" ]; then
  pw="$(secret BAIKAL_FEEDS_PASSWORD 24)"
  printf '%s\n' "$pw" | python3 "$HERE/60-baikal.py" user feeds "feeds@$DOMAIN" "Imported schedules" - >/dev/null   # password on stdin
  cat > "$F/ics-feeds.json" <<EOF
{
  "baikal_url": "http://dav.$LAN_DOMAIN/dav.php",
  "baikal_db": "$HEARDOS_ROOT/data/baikal/specific/db/db.sqlite",
  "user": "feeds",
  "password": "$pw",
  "share_with": {"principals/$ADMIN_USER": "mailto:$ADMIN_EMAIL"},
  "strip_alarms": true,
  "feeds": []
}
EOF
  chown "$U:$U" "$F/ics-feeds.json"; chmod 600 "$F/ics-feeds.json"; msg "created $F/ics-feeds.json — add feeds (see the manual's appendix), then re-run this script"
fi
cat > /etc/systemd/system/heardos-feeds.service <<EOF
[Unit]
Description=HeardOS calendar feed import (all feeds)
After=docker.service network-online.target
[Service]
Type=oneshot
User=$U
Group=$U
SupplementaryGroups=www-data
WorkingDirectory=$F
ExecStart=/usr/bin/python3 $F/ics-feeds-sync.py
UMask=0007
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
ReadWritePaths=$F -$BDB
EOF
cat > /etc/systemd/system/heardos-feeds.timer <<'EOF'
[Unit]
Description=Run the HeardOS calendar feed import every 15 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=15min
RandomizedDelaySec=60
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload; systemctl enable --now heardos-feeds.timer >/dev/null
msg "validating config with one run (as $U)"; systemctl start heardos-feeds.service || { journalctl -u heardos-feeds.service -n 30 --no-pager; warn "the importer reported errors (see above)"; }
systemctl list-timers heardos-feeds.timer --no-pager | head -2
msg "logs: journalctl -u heardos-feeds.service ; per-feed test: cd $F && runuser -u $U -- python3 ics-feeds-sync.py --feed <id>"
