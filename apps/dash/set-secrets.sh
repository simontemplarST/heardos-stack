#!/usr/bin/env bash
# Pushes the dashboard's config + secrets into the Runtipi custom app `dash:_user` and restarts it.
# Reads from ~/.config/credentials.env:  MEALIE_HA_TOKEN (required), ACTUAL_API_PASSWORD, HOMEBOX_DASHBOARD_API_KEY (optional:
# empty = that card shows "not configured"), RUNTIPI_ADMIN_USER/PASSWORD.  Get HOMEBOX_DASHBOARD_API_KEY from Homebox → your
# profile → API keys → create (name heardos-dash, expiry far out) and add it to credentials.env first.
# Talks to the Runtipi API directly: RUNTIPI_BASE=http://192.168.10.20 on the LAN (default), http://10.42.0.11 over Nebula.
# Card "open ↗" links = *_PUBLIC_URL; since 2026-10-14 they go through the cloud.example.com/<authentik-slug> front door.
set -euo pipefail
set -a; . ~/.config/credentials.env; set +a
: "${MEALIE_HA_TOKEN:?MEALIE_HA_TOKEN missing}"
ACTUAL_API_PASSWORD="${ACTUAL_API_PASSWORD:-}"; HOMEBOX_DASHBOARD_API_KEY="${HOMEBOX_DASHBOARD_API_KEY:-}"
B="${RUNTIPI_BASE:-http://192.168.10.20}"
# secrets never go on a command line: temp files are mktemp (0600), values reach python via its environment and curl
# reads its request bodies from those files (-d @file)
umask 077
CJ=$(mktemp); CFG=$(mktemp); LOGIN=$(mktemp); trap 'rm -f "$CJ" "$CFG" "$LOGIN"' EXIT
api() { curl -s -m 120 -b "$CJ" -c "$CJ" -H "Content-Type: application/json" -H "Origin: $B" -H "Referer: $B/" "$@"; }
M="$MEALIE_HA_TOKEN" A="$ACTUAL_API_PASSWORD" H="$HOMEBOX_DASHBOARD_API_KEY" python3 - > "$CFG" <<'EOF'
import json,os
m,a,h=os.environ["M"],os.environ["A"],os.environ["H"]
cfg={"services":{"dash":{"image":"heardos-dash:0.1","pull_policy":"never","restart":"unless-stopped",
 "environment":{"TZ":"America/Chicago","CACHE_SECONDS":"180",
  "MEALIE_URL":"http://mealie-1_migrated-mealie-1-1:9000","MEALIE_PUBLIC_URL":"https://cloud.example.com/mealie","MEALIE_TOKEN":m,
  "ACTUAL_URL":"http://actual-budget_migrated-actual-budget-1:5006","ACTUAL_PUBLIC_URL":"https://cloud.example.com/actual-budget",
  "ACTUAL_SYNC_ID":"00000000-0000-4000-8000-000000000000","ACTUAL_PASSWORD":a,
  "HOMEBOX_URL":"http://homebox_migrated-homebox-1:7745","HOMEBOX_PUBLIC_URL":"https://cloud.example.com/homebox","HOMEBOX_API_KEY":h},
 "volumes":["${APP_DATA_DIR}/data:/data"],"x-runtipi":{"internal_port":8080,"is_main":True}}},"x-runtipi":{"schema_version":2}}
print(json.dumps({"config":cfg}))
EOF
U="$RUNTIPI_ADMIN_USER" P="$RUNTIPI_ADMIN_PASSWORD" python3 -c 'import json,os; print(json.dumps({"username":os.environ["U"],"password":os.environ["P"]}))' > "$LOGIN"
api -o /dev/null -w "login %{http_code}\n" -d @"$LOGIN" "$B/api/auth/login"; rm -f "$LOGIN"
api -o /dev/null -w "patch %{http_code}\n" -X PATCH -d @"$CFG" "$B/api/custom-apps/dash:_user"; rm -f "$CFG"
api -o /dev/null -w "restart %{http_code}\n" -X POST -d '{}' "$B/api/app-lifecycle/dash:_user/restart"
sleep 25
api "$B/api/apps/dash:_user" | python3 -c 'import json,sys; print("status", json.load(sys.stdin)["app"]["status"])'
