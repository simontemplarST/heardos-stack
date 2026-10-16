#!/usr/bin/env bash
# run_e2e.sh — full install against real infrastructure, then teardown. Costs a few cents per run (hourly VPS billing).
#   HOME_HOST=<ip of a THROWAWAY Ubuntu 24.04 machine with your key or HOST_SSH_PASSWORD in the vault>
#   PLAN=tests/e2e/plan.json (copy plan.example.json; set DOMAIN to a test zone your CF_API_TOKEN can edit)
# Never point HOME_HOST at a machine you care about: the kit installs Docker, rewrites ufw rules and deploys stacks.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${HOME_HOST:?set HOME_HOST to a throwaway Ubuntu 24.04 machine}" "${PLAN:=tests/e2e/plan.json}"
ID="e2e-$(date +%Y%m%d-%H%M%S)"
tmp="$(mktemp)"; python3 - "$PLAN" "$HOME_HOST" "$ID" > "$tmp" <<'PY'
import json, sys; p = json.load(open(sys.argv[1])); p["HOST_LAN_IP"] = sys.argv[2]; p["install_id"] = sys.argv[3]; print(json.dumps(p))
PY
cleanup() { echo "== teardown"; .venv/bin/python -m heardos_installer destroy "$ID" --yes || true; rm -f "$tmp"; }
trap cleanup EXIT
echo "== install $ID"
.venv/bin/python -m heardos_installer install --plan "$tmp" --yes
echo "== posture"
.venv/bin/python -m heardos_installer status "$ID"
echo "== health + port scan"
.venv/bin/python -m heardos_installer health "$ID" --scan
