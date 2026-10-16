#!/usr/bin/env bash
# 61-feeds-timer.sh — run ON THE HOST as root. Installs the calendar feed importer and a systemd timer (every 15 min).
# Config: $HEARDOS_ROOT/feeds/ics-feeds.json (created from the example on first run; edit it, then re-run to validate).
. "$(dirname "$0")/lib/common.sh"; require_root; load_env
F="$HEARDOS_ROOT/feeds"; install -d -m 700 "$F" "$F/.ics-feeds-state"
install -m 700 "$HERE/ics-feeds-sync.py" "$F/ics-feeds-sync.py"
if [ ! -f "$F/ics-feeds.json" ]; then
  pw="$(secret BAIKAL_FEEDS_PASSWORD 24)"
  python3 "$HERE/60-baikal.py" user feeds "feeds@$DOMAIN" "Imported schedules" "$pw" >/dev/null
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
  chmod 600 "$F/ics-feeds.json"; msg "created $F/ics-feeds.json — add feeds (see the manual's appendix), then re-run this script"
fi
cat > /etc/systemd/system/heardos-feeds.service <<EOF
[Unit]
Description=HeardOS calendar feed import (all feeds)
After=docker.service network-online.target
[Service]
Type=oneshot
WorkingDirectory=$F
ExecStart=/usr/bin/python3 $F/ics-feeds-sync.py
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
msg "validating config with one run"; ( cd "$F" && python3 ics-feeds-sync.py ) || warn "the importer reported errors (see above)"
systemctl list-timers heardos-feeds.timer --no-pager | head -2
msg "logs: journalctl -u heardos-feeds.service ; per-feed test: cd $F && python3 ics-feeds-sync.py --feed <id>"
