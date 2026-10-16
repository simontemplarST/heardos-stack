# Authentik hardening steps (findings #12–#16)

These change who can administer SSO, so they are left for a human. Each is a few minutes in the Authentik
admin UI (https://auth.example.com/if/admin/) or one API call with the admin token.

## 12. Retire `akadmin`

`akadmin` is Authentik's stock superuser: no MFA, last login 2026-09-29, and it owns the automation token
`claude-setup-20260928` that the kit scripts use as `AUTHENTIK_TOKEN`.

1. Directory → Users → Create service account: username `heardos-automation`, "Create group" off, expiring
   token **on**, 90 days. Copy the token it shows.
2. Add `heardos-automation` to the group **authentik Admins** (it needs superuser to manage apps/providers).
3. In `~/.config/credentials.env` replace `AUTHENTIK_TOKEN=` with the new token; run
   `~/heardos-install/security/security-check.sh` to prove it works.
4. Directory → Tokens: delete `claude-setup-20260928`.
5. Directory → Users → `akadmin` → Deactivate (do **not** delete; it stays as an inactive break-glass you
   can re-enable from the CLI: `docker exec -it authentik-1_migrated-authentik-server-1 ak shell` … or from
   another superuser).
6. Put a calendar reminder for the 90-day token rotation (step 1 again, then steps 3–4).

## 13. `carol` is a superuser

Directory → Groups → authentik Admins → Users. If `carol` should not administer SSO, remove them there;
their app access comes from the `app-*` groups and is unaffected.

## 14. MFA for `bob` and `dave`

The login flow has an MFA validation stage, but users without a device pass straight through. Each user
enrols at https://auth.example.com/if/user/#/settings → MFA devices → Passkey (phone or Bitwarden).
To *force* it: Flows → `default-authentication-mfa-validation` stage → "Not configured action" = **Configure**
(sends device-less users into enrolment) instead of Skip.

`dave` has never logged in and reported "request has been denied: flow does not apply to current user" from
mobile data — check the policy bound to the authentication flow (Flows → default-authentication-flow →
Policy bindings); the scratchpad scripts `ak-mfa-fix.py` / `ak-mfa-group.py` from 2026-10-14 are where this
was last touched.

## 15. Non-expiring automation token

Covered by step 12.1 (expiring token on the service account).

## 16. Plain-http redirect URIs

Applications → Providers → `homebox` → Redirect URIs: delete `http://homebox.example.com/api/v1/users/login/oidc/callback`
(the https one stays). The `http://*.192-168-10-20.sslip.io/...` entries are the LAN-only logins and are fine.
