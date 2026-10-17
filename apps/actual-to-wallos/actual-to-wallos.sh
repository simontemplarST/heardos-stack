#!/bin/bash
# actual-to-wallos.sh — CronMaster entry point (runs on the CT 108 host, where there is no node).
# Runs actual-to-wallos.mjs inside the heardos-dash image (node 22 + @actual-app/api) on the Runtipi network,
# so Actual and Wallos are reached by container name. Pass --dry-run / --verbose through.
set -u
SCRIPTS=/root/runtipi/app-data/_user/cronmaster/scripts
CACHE=/root/runtipi/app-data/_user/cronmaster/data/actual-cache
ENV_FILE="$SCRIPTS/actual-to-wallos.env"
[ -r "$ENV_FILE" ] || { echo "missing $ENV_FILE"; exit 2; }
mkdir -p "$CACHE"; chown 1000:1000 "$CACHE"   # the image runs as uid 1000
exec docker run --rm --network proxy --env-file "$ENV_FILE" \
  -v "$SCRIPTS/actual-to-wallos.mjs:/app/sync/actual-to-wallos.mjs:ro" -v "$SCRIPTS/actual-to-wallos-logos.json:/app/sync/actual-to-wallos-logos.json:ro" -v "$CACHE:/data/actual-cache" \
  -w /app heardos-dash:0.1 node /app/sync/actual-to-wallos.mjs "$@"
