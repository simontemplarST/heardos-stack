# HeardOS Authentik business flows

One group per behaviour. Put a user in the group and it applies; remove them and it stops. Built and re-asserted by
`authentik/business-flows.py` (idempotent; `--dry-run` prints the plan). Needs `set -a; . ~/.config/credentials.env; set +a`.

| Group | Members get | Where it acts |
|---|---|---|
| `flow-login-blocked` | "This account is suspended" right after entering their username. Account stays active, nothing is deleted. | login flow, deny stage at order 15 |
| `flow-mfa-required` | Must pass MFA every login; with no device they are sent into enrolment (passkey or TOTP) before they get a session. Email codes do **not** count here. Members of `authentik Admins` get exactly this treatment too, group or not (policy `mfa-required-for-admins`). Everyone else is only asked for MFA if they already have a device (email codes count for them). | login flow, `heardos-mfa-required` (order 31) vs the optional stage (order 30) |
| `flow-session-8h` | Session ends after 8 hours, no "remember me". | login flow, `heardos-login-8h` (order 99) instead of the default login stage |
| `flow-recovery-allowed` | May use "Forgot password" (email reset through Resend). Non-members are denied at that step. Members with an MFA device must also pass it; admins need a passkey **and** an authenticator-app code. | recovery flow, deny stage at order 15, MFA at 35/36/37 |
| `flow-self-service` | May change their own password and profile from the user portal. Non-members see a denial there. | user-settings and password-change flows |
| `flow-invited` | Not a permission: the group new accounts land in when created through the invitation sign-up. | `heardos-enrollment-write` |
| `flow-explicit-consent` | Sees a consent screen the first time they open each application, re-asked every 4 weeks. Everyone else is authorized silently. | `heardos-provider-authorization`, set on every provider |

Pre-populated: `flow-self-service` with all current users (re-asserted on every run). `flow-recovery-allowed` is filled
**once, when the group is created**, with every internal user except members of `authentik Admins` and superusers, so a
re-run never undoes a revocation; the script warns if admins are in it. `flow-mfa-required` is the old `mfa-required`
group renamed. `42-ak-app.py user add NAME --admin` puts a new admin in `authentik Admins` and `flow-mfa-required`.

## Flows

- **Login** `default-authentication-flow`: identification → deny if blocked → password → MFA → user login (8 h or
  default per group). MFA:
  - order 30 `default-authentication-mfa-validation`, *skip* if no device, TOTP/passkey/recovery code/email code; bound
    (mode **all**) to NOT `flow-mfa-required`, NOT `mfa-required-for-admins`, and the stock passkey-skip policy.
  - order 31 `heardos-mfa-required`, *configure* (enrol) if no device, TOTP/passkey/recovery code only; bound (mode
    **any**) to `flow-mfa-required` OR `mfa-required-for-admins`. The passkey-skip policy must not be on this binding
    (in "any" mode it is true for everyone). `41-authentik-bootstrap.py` and `business-flows.py` both build this layout.
  - The separate passkey flow `heardos-passwordless` (user-verified WebAuthn) does not go through these stages.
- **Recovery** `default-recovery-flow` (`authentik/recovery-flow.py`): identification → 15 deny unless
  `flow-recovery-allowed` → 20 email link → 30 new password → 35 `heardos-recovery-mfa` (non-admins: TOTP/passkey/recovery
  code if they have one, skipped if not) → 36 `heardos-recovery-mfa-admin` (`authentik Admins`: a **passkey**) → 37
  `heardos-recovery-totp-admin` (`authentik Admins`: then an **authenticator-app code**); each admin step is **denied**
  when the admin has no device of that kind, so an admin must enrol both before recovery works → 40 save → 50 login, which **ends every other session** of that user. Email codes are not accepted (the
  link already proves the mailbox). The email stage does **not** re-activate a deactivated account
  (`activate_user_on_success` false), so offboarded users stay off.
- **Invitation sign-up** `heardos-enrollment-invitation`: invitation token → username/name/email/password (password policy
  enforced) → account created *inactive* in `flow-invited` → activation email → login. Create invitations under
  Directory → Invitations, pick this flow; the link is `https://auth.example.com/if/flow/heardos-enrollment-invitation/?itoken=…`.
  Without a valid token the flow refuses, so there is no open sign-up.
- **Authorization** `heardos-provider-authorization`: consent stage gated by `flow-explicit-consent`; all providers use it.
- **Email code MFA** `heardos-authenticator-email-setup`: users enrol an email code device from the portal (MFA devices →
  Email code); accepted only by the optional stage (order 30), never for admins, `flow-mfa-required` members, or during
  recovery. The code is not in the email's preview line (lock screens show that).

## Admin recipes

- Suspend someone: add to `flow-login-blocked`. Reinstate: remove.
- Offboard: add to `flow-login-blocked`, then Directory → Users → deactivate when ready.
- Onboard: Directory → Invitations → create (flow `heardos-enrollment-invitation`, single use, expiry) → send the link.
- Force MFA for a contractor on shared PCs: add to `flow-mfa-required` and `flow-session-8h`.
- Someone changed their email address through self-service: set the user attribute `email_verified: false` (Directory →
  Users → Attributes) until the address is confirmed. The `heardos-email-verified` scope mapping tells apps the address
  is verified unless that attribute is false; apps that link accounts by email (Nextcloud, Immich, Mealie…) trust it,
  and Mealie refuses sign-in while it is false.

## Automation account and token rotation

The kit's scripts do not use akadmin. `40-authentik-stack.sh` uses the one-time bootstrap token only to create the
service account `heardos-automation` (user type *service account*, member of the group `heardos-automation`, which has
**superuser** rights: the scripts edit flows, stages, policies, brands, providers, outposts, users and tokens, and
Authentik's RBAC offers no practical narrower role for that set). Its API token expires after **90 days**. Then the
bootstrap variables are removed from the stack `.env` (Authentik only reads them on first start) and akadmin's
`authentik-bootstrap-token` is deleted. akadmin's password stays in `secrets/secrets.env` as break-glass.

Rotate before expiry (the old automation token is revoked once the new one is verified):

    HEARDOS_ENV=/opt/heardos-kit/generic/heardos.env python3 /opt/heardos-kit/generic/42-ak-app.py token status
    HEARDOS_ENV=/opt/heardos-kit/generic/heardos.env python3 /opt/heardos-kit/generic/42-ak-app.py token rotate --write-env [--days 90]

`--write-env` replaces `AUTHENTIK_TOKEN` in the plan file; the new value is also printed as a `HEARDOS_OUTPUT` line
(masked by the installer). Copy it into the installer vault / laptop plan file. If it already expired, sign in as akadmin,
create a short-lived token under Directory → Tokens, put it in the plan file and run `token rotate --write-env`.

## Policy mode on stage bindings

Every HeardOS stage binding uses policy engine mode **all** (every bound group/policy must pass), except the required-MFA
binding at order 31, which is **any** (group OR admin policy) and therefore carries no passkey-skip policy. The default is "any",
which let the passkey-skip expression policy alone run the MFA stages regardless of the group, so a device-less user was
pushed into MFA enrolment (found by `authentik/test-flows.py` on 2026-10-16). `business-flows.py` now sets it explicitly.

## Why group bindings instead of expression policies

Stage bindings carry the group directly (`PolicyBinding.group`, with `negate` for "everyone except"), evaluated when the
stage is reached (`evaluate_on_plan=false`, `re_evaluate_policies=true`) because the user is not known at flow start.
The old `mfa-required-only` expression policy is removed once nothing references it. `mfa-required-for-admins` is
the one expression policy kept: it ties MFA to the admin role, so a new admin is covered even before anyone adds them to
`flow-mfa-required`.
