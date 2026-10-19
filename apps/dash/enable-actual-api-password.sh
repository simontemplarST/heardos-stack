#!/usr/bin/env bash
# Gives Actual Budget a server password that ONLY API clients use, while the web UI keeps OpenID as its sole login.
# Why: @actual-app/api can only log in with loginMethod=password, and Actual's /change-password route refuses
# OpenID sessions, so the row has to be written straight into account.sqlite (auth table, active=0).
# Actual's web UI can't do this, so run it by hand:  bash enable-actual-api-password.sh
# The password never appears on a command line: it travels as the first line of ssh's stdin, the remote bash reads it
# into PW, and the rest of stdin is the (fixed, quoted) script; docker exec gets it with `-e PW` (name only).
set -euo pipefail
ACTUAL_API_PASSWORD=$(set -a; . ~/.config/credentials.env; printf '%s' "${ACTUAL_API_PASSWORD:-}")
[ -n "$ACTUAL_API_PASSWORD" ] || { echo "ACTUAL_API_PASSWORD missing in ~/.config/credentials.env" >&2; exit 1; }
case "$ACTUAL_API_PASSWORD" in *$'\n'*) echo "ACTUAL_API_PASSWORD must be a single line" >&2; exit 1 ;; esac

{ printf '%s\n' "$ACTUAL_API_PASSWORD"; cat <<'REMOTE'
set -e
C=actual-budget_migrated-actual-budget-1
DB=/root/runtipi/app-data/migrated/actual-budget/data/server-files/account.sqlite
cp "$DB" "$DB.bak-$(date +%Y%m%d-%H%M%S)"
H=$(docker exec -e PW "$C" node -e 'process.stdout.write(require("bcrypt").hashSync(process.env.PW, 12))' </dev/null)
H="$H" python3 - "$DB" <<'EOF'
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.execute("DELETE FROM auth WHERE method='password'")
c.execute("INSERT INTO auth (method, display_name, extra_data, active) VALUES ('password','Password',?,0)", (os.environ["H"],)); c.commit()
print("auth rows now:", c.execute("select method, active from auth").fetchall())
EOF
echo "-- API login test:"
docker exec -e PW "$C" node -e 'fetch("http://localhost:5006/account/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({loginMethod:"password",password:process.env.PW})}).then(r=>r.json()).then(j=>console.log(j.status, j.data&&j.data.token?"token ok":JSON.stringify(j)))' </dev/null
echo "-- web UI login methods (should still be openid only):"
docker exec "$C" node -e 'fetch("http://localhost:5006/account/login-methods").then(r=>r.text()).then(console.log)' </dev/null
REMOTE
} | ssh root@192.168.10.2 "pct exec 108 -- bash -c 'IFS= read -r PW; export PW; exec bash -s'"
echo "Done. Now run: bash ~/heardos-install/dash/set-secrets.sh"
