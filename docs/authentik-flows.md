# HeardOS Authentik business flows

One group per behaviour. Put a user in the group and it applies; remove them and it stops. Built and re-asserted by
`authentik/business-flows.py` (idempotent; `--dry-run` prints the plan). Needs `set -a; . ~/.config/credentials.env; set +a`.

| Group | Members get | Where it acts |
|---|---|---|
| `flow-login-blocked` | "This account is suspended" right after entering their username. Account stays active, nothing is deleted. | login flow, deny stage at order 15 |
| `flow-mfa-required` | Must pass MFA every login; with no device they are sent into enrolment (passkey, TOTP, or email code) before they get a session. Everyone else is only asked for MFA if they already have a device. | login flow, `heardos-mfa-required` (order 31) vs the optional stage (order 30) |
| `flow-session-8h` | Session ends after 8 hours, no "remember me". | login flow, `heardos-login-8h` (order 99) instead of the default login stage |
| `flow-recovery-allowed` | May use "Forgot password" (email reset through Resend). Non-members are denied at that step. | recovery flow, deny stage at order 15 |
| `flow-self-service` | May change their own password and profile from the user portal. Non-members see a denial there. | user-settings and password-change flows |
| `flow-invited` | Not a permission: the group new accounts land in when created through the invitation sign-up. | `heardos-enrollment-write` |
| `flow-explicit-consent` | Sees a consent screen the first time they open each application, re-asked every 4 weeks. Everyone else is authorized silently. | `heardos-provider-authorization`, set on every provider |

Pre-populated: `flow-recovery-allowed` and `flow-self-service` with all current users (so nothing changes for them);
`flow-mfa-required` is the old `mfa-required` group renamed (alice, carol, akadmin, dave).

## Flows

- **Login** `default-authentication-flow`: identification → deny if blocked → password → MFA (optional or required per
  group; passkey logins skip it) → user login (8 h or default per group).
- **Recovery** `default-recovery-flow`: identification → deny unless allowed → email link → new password → login.
- **Invitation sign-up** `heardos-enrollment-invitation`: invitation token → username/name/email/password (password policy
  enforced) → account created *inactive* in `flow-invited` → activation email → login. Create invitations under
  Directory → Invitations, pick this flow; the link is `https://auth.example.com/if/flow/heardos-enrollment-invitation/?itoken=…`.
  Without a valid token the flow refuses, so there is no open sign-up.
- **Authorization** `heardos-provider-authorization`: consent stage gated by `flow-explicit-consent`; all providers use it.
- **Email code MFA** `heardos-authenticator-email-setup`: users enrol an email code device from the portal (MFA devices →
  Email code); allowed as a second factor alongside passkeys, TOTP and recovery codes.

## Admin recipes

- Suspend someone: add to `flow-login-blocked`. Reinstate: remove.
- Offboard: add to `flow-login-blocked`, then Directory → Users → deactivate when ready.
- Onboard: Directory → Invitations → create (flow `heardos-enrollment-invitation`, single use, expiry) → send the link.
- Force MFA for a contractor on shared PCs: add to `flow-mfa-required` and `flow-session-8h`.

## Policy mode on stage bindings

Every HeardOS stage binding uses policy engine mode **all** (every bound group/policy must pass). The default is "any",
which let the passkey-skip expression policy alone run the MFA stages regardless of the group, so a device-less user was
pushed into MFA enrolment (found by `authentik/test-flows.py` on 2026-10-16). `business-flows.py` now sets it explicitly.

## Why group bindings instead of expression policies

Stage bindings carry the group directly (`PolicyBinding.group`, with `negate` for "everyone except"), evaluated when the
stage is reached (`evaluate_on_plan=false`, `re_evaluate_policies=true`) because the user is not known at flow start.
The old `mfa-required-only` expression policy is removed once nothing references it.
