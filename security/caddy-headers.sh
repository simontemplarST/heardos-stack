#!/usr/bin/env bash
# caddy-headers.sh — add nosniff + Referrer-Policy and drop the Server header on the
# public edge. Stages the file, validates it, backs up, swaps, reloads, and verifies. Idempotent.
set -euo pipefail
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
scp -q nebula-lh:/etc/caddy/Caddyfile "$T/Caddyfile"
python3 - "$T/Caddyfile" <<'EOF'
import sys; p=sys.argv[1]; s=open(p).read()
old='\theader Strict-Transport-Security "max-age=31536000"\n'
new='\theader {\n\t\tStrict-Transport-Security "max-age=31536000"\n\t\tX-Content-Type-Options nosniff\n\t\tReferrer-Policy strict-origin-when-cross-origin\n\t\t-Server\n\t}\n'
print("header blocks to upgrade:", s.count(old)); open(p,"w").write(s.replace(old,new))
EOF
scp -q "$T/Caddyfile" nebula-lh:/etc/caddy/Caddyfile.new
ssh nebula-lh 'cd /etc/caddy && caddy validate --config Caddyfile.new --adapter caddyfile 2>&1 | tail -1 && cp -p Caddyfile "Caddyfile.bak-$(date +%Y%m%d)" && mv Caddyfile.new Caddyfile && systemctl reload caddy && sleep 3 && for h in auth photos cloud; do printf "%-7s " $h; curl -s -o /dev/null -D - -m 10 https://$h.example.com/ | tr -d "\r" | grep -i -E "^(x-content-type-options|referrer-policy|server:|strict-transport)" | tr "\n" " "; echo; done'
cp "$T/Caddyfile" ~/heardos-install/Caddyfile && echo "kit copy updated"
