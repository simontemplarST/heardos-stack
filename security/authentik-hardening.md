# Authentik hardening steps

These change who can administer SSO, so make them by hand. Each is a few minutes in the Authentik
admin UI (https://auth.example.com/if/admin/) or one API call with the admin token.

## Retire `akadmin` as the automation account

`akadmin` is Authentik's stock superuser. Don't leave it as an interactive login, and don't let it own a
non-expiring token for the kit scripts (`AUTHENTIK_TOKEN`).

1. Directory → Users → Create service account: username `heardos-automation`, "Create group" off, expiring
   token **on**, 90 days. Copy the token it shows.
2. Add `heardos-automation` to the group **authentik Admins** (it needs superuser to manage apps/providers).
3. In `~/.config/credentials.env` replace `AUTHENTIK_TOKEN=` with the new token; run
   `security/security-check.sh` to prove it works.
4. Directory → Tokens: delete the old automation token.
5. Directory → Users → `akadmin` → Deactivate (do **not** delete; it stays as an inactive break-glass you
   can re-enable from the CLI: `docker exec -it <authentik-server container> ak shell` … or from
   another superuser).
6. Put a calendar reminder for the 90-day token rotation (step 1 again, then steps 3–4).

## Review superusers

Directory → Groups → authentik Admins → Users. Remove anyone who should not administer SSO; their app
access comes from the `app-*` groups and is unaffected.

## Require MFA

By default the login flow's MFA validation stage lets users without a device pass straight through. Each user
enrols at https://auth.example.com/if/user/#/settings → MFA devices → Passkey (phone or Bitwarden).
To *force* it: Flows → `default-authentication-mfa-validation` stage → "Not configured action" = **Configure**
(sends device-less users into enrolment) instead of Skip.

If a user sees "request has been denied: flow does not apply to current user", check the policy bound to the
authentication flow (Flows → default-authentication-flow → Policy bindings).

## Plain-http redirect URIs

Applications → Providers → each provider → Redirect URIs: delete any `http://` URI on a public hostname
(the https one stays). `http://*.<lan-ip>.sslip.io/...` entries are LAN-only logins and are fine.
