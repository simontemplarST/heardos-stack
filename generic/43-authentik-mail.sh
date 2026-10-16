#!/usr/bin/env bash
# 43-authentik-mail.sh — run ON THE HOST as root after 40. Points Authentik's outgoing mail at Resend or any SMTP server,
# restarts Authentik and sends a test message to ADMIN_EMAIL.
#   MAIL_MODE=resend RESEND_API_KEY=re_... [MAIL_FROM="Name <noreply@domain>"] 43-authentik-mail.sh
#   MAIL_MODE=smtp SMTP_HOST=… SMTP_PORT=587 SMTP_USER=… SMTP_PASSWORD=… [MAIL_FROM=…] 43-authentik-mail.sh
# Port 465 = implicit TLS (USE_SSL), anything else = STARTTLS (USE_TLS). Values go only into the stack's .env (0600).
. "$(dirname "$0")/lib/common.sh"; require_root; load_env
d="$HEARDOS_ROOT/stacks/authentik"; [ -f "$d/.env" ] || die "run 40-authentik-stack.sh first"
from="${MAIL_FROM:-${BRAND_TITLE:-HeardOS} <noreply@$DOMAIN>}"
case "${MAIL_MODE:?resend or smtp}" in
  resend) : "${RESEND_API_KEY:?}"; host=smtp.resend.com port=465 user=resend pass="$RESEND_API_KEY" ;;
  smtp)   : "${SMTP_HOST:?}" "${SMTP_USER:?}" "${SMTP_PASSWORD:?}"; host="$SMTP_HOST" port="${SMTP_PORT:-587}" user="$SMTP_USER" pass="$SMTP_PASSWORD" ;;
  *) die "MAIL_MODE must be resend or smtp" ;;
esac
if [ "$port" = 465 ]; then ssl=true tls=false; else ssl=false tls=true; fi
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }   # single-quote for the compose .env
sed -i '/^EMAIL_\(HOST\|PORT\|USERNAME\|PASSWORD\|USE_TLS\|USE_SSL\|FROM\)=/d' "$d/.env"
{ echo "EMAIL_HOST=$host"; echo "EMAIL_PORT=$port"; echo "EMAIL_USERNAME=$(q "$user")"; echo "EMAIL_PASSWORD=$(q "$pass")"
  echo "EMAIL_USE_TLS=$tls"; echo "EMAIL_USE_SSL=$ssl"; echo "EMAIL_FROM=$(q "$from")"; } >> "$d/.env"
chmod 600 "$d/.env"
if dry; then msg "[dry-run] would redeploy authentik with mail via $host:$port and send a test to $ADMIN_EMAIL"; exit 0; fi
deploy_stack authentik
wait_http "http://$HOST_LAN_IP:9000/-/health/ready/" 600 || die "Authentik did not come back"
w="$(docker ps -q --filter label=com.docker.compose.project=authentik --filter label=com.docker.compose.service=worker | head -1)"
[ -n "$w" ] || die "Authentik worker container not found"
for i in 1 2 3; do
  if docker exec "$w" ak test_email "$ADMIN_EMAIL" >/tmp/ak-mail.log 2>&1; then msg "test email sent to $ADMIN_EMAIL via $host"; out MAIL_OK 1; exit 0; fi
  sleep 20
done
tail -5 /tmp/ak-mail.log >&2; die "Authentik could not send mail via $host:$port (check the key/password and that the sender domain is verified)"
