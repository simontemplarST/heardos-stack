#!/bin/bash
# actual-to-wallos-fill-wallos-user.sh USER — run ON CT 108 as root. Copies that Wallos user's existing API key into
# actual-to-wallos.env as WALLOS_API_KEY_<USER>, so the sync also fills their Wallos account. Prints only the length.
set -e
U="${1:?wallos username}"; VAR="WALLOS_API_KEY_$(echo "$U" | tr 'a-z-' 'A-Z_')"
E=/root/runtipi/app-data/_user/cronmaster/scripts/actual-to-wallos.env
WK=$(docker exec -e WU="$U" wallos php -r '$db=new SQLite3("/var/www/html/db/wallos.db",SQLITE3_OPEN_READONLY); $s=$db->prepare("select api_key from user where username=:u"); $s->bindValue(":u",getenv("WU")); $r=$s->execute()->fetchArray(); echo $r ? $r[0] : "";')
[ -n "$WK" ] || { echo "no Wallos API key for user $U (they must sign in to Wallos once; the key is created with the account)"; exit 1; }
grep -q "^$VAR=" "$E" && sed -i "s|^$VAR=.*|$VAR=$WK|" "$E" || printf '# %s'"'"'s Wallos account gets the same subscriptions\n%s=%s\n' "$U" "$VAR" "$WK" >> "$E"
chmod 600 "$E"
echo "env updated: $VAR (${#WK} chars)"
