#!/usr/bin/env bash
# Gives Actual Budget a server password that ONLY API clients use, while the web UI keeps OpenID as its sole login.
# Why: @actual-app/api can only log in with loginMethod=password, and Actual's /change-password route refuses
# OpenID sessions, so the row has to be written straight into account.sqlite (auth table, active=0).
# Actual's web UI can't do this, so run it by hand:  bash enable-actual-api-password.sh
set -euo pipefail
set -a; . ~/.config/credentials.env; set +a
: "${ACTUAL_API_PASSWORD:?ACTUAL_API_PASSWORD missing in ~/.config/credentials.env}"
ssh root@192.168.10.2 "pct exec 108 -- bash -c '
set -e; C=actual-budget_migrated-actual-budget-1; DB=/root/runtipi/app-data/migrated/actual-budget/data/server-files/account.sqlite
cp \$DB \$DB.bak-\$(date +%Y%m%d-%H%M%S)
H=\$(docker exec -e PW=\"$ACTUAL_API_PASSWORD\" \$C node -e \"process.stdout.write(require(\\\"bcrypt\\\").hashSync(process.env.PW, 12))\")
python3 - \"\$DB\" \"\$H\" <<\"EOF\"
import sqlite3,sys
c=sqlite3.connect(sys.argv[1]); c.execute(\"DELETE FROM auth WHERE method=\\\"password\\\"\")
c.execute(\"INSERT INTO auth (method, display_name, extra_data, active) VALUES (\\\"password\\\",\\\"Password\\\",?,0)\", (sys.argv[2],)); c.commit()
print(\"auth rows now:\", c.execute(\"select method, active from auth\").fetchall())
EOF
echo \"-- API login test:\"; docker exec -e PW=\"$ACTUAL_API_PASSWORD\" \$C node -e \"fetch(\\\"http://localhost:5006/account/login\\\",{method:\\\"POST\\\",headers:{\\\"Content-Type\\\":\\\"application/json\\\"},body:JSON.stringify({loginMethod:\\\"password\\\",password:process.env.PW})}).then(r=>r.json()).then(j=>console.log(j.status, j.data&&j.data.token?\\\"token ok\\\":JSON.stringify(j)))\"
echo \"-- web UI login methods (should still be openid only):\"; docker exec \$C node -e \"fetch(\\\"http://localhost:5006/account/login-methods\\\").then(r=>r.text()).then(console.log)\"
'"
echo "Done. Now run: bash ~/heardos-install/dash/set-secrets.sh"
