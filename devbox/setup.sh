#!/usr/bin/env bash
# setup.sh — run once INSIDE the heardos-dev distrobox (re-runnable): installer venv + a quick self-check.
set -euo pipefail
[ -n "${CONTAINER_ID:-}" ] || { echo "run this inside the box: distrobox enter heardos-dev" >&2; exit 1; }
cd "$(dirname "$0")/.."
v=installer/.venv
if ! "$v/bin/python" -c 'import imgui_bundle' 2>/dev/null; then
  python3 -m venv --clear "$v"
fi
"$v/bin/pip" install -q --upgrade pip
"$v/bin/pip" install -q -e "installer[dev]"
echo "== tools"
missing=0
for t in git gh ssh rsync jq dig sqlite3 envsubst aria2c nmap shellcheck caddy nebula-cert step magick python3; do
  if ! command -v "$t" >/dev/null; then printf '  %-11s MISSING\n' "$t"; missing=1; continue; fi
  case "$t" in ssh) v="$(ssh -V 2>&1)";; dig) v="$(dig -v 2>&1)";; nebula-cert) v="$(nebula-cert -version 2>&1)";;
    caddy) v="$(caddy version 2>&1)";; *) v="$("$t" --version 2>&1)";; esac
  printf '  %-11s %s\n' "$t" "$(printf '%s' "$v" | head -1 | cut -c1-60)"
done
[ "$missing" = 0 ] || echo "some tools are missing: rebuild with distrobox assemble create --replace --file devbox/distrobox.ini"
echo "== installer unit tests"; (cd installer && .venv/bin/pytest -q tests/unit | tail -1)
[ -f "$HOME/.config/credentials.env" ] || echo "note: ~/.config/credentials.env not found — scripts that call your services need it (mode 0600)"
echo "ready: cd installer && .venv/bin/heardos   (GUI)   |   .venv/bin/heardos-cli --help"
