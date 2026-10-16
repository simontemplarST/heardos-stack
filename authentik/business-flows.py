#!/usr/bin/env python3
"""business-flows.py — the standard set of business flows for HeardOS Authentik, each controlled by ONE group.

Put a user in the group and the behaviour applies to them; remove them and it stops. Idempotent: re-running only
creates what is missing and re-asserts settings. Needs AUTHENTIK_TOKEN (admin) in ~/.config/credentials.env.

  group                   what membership means
  flow-login-blocked      cannot sign in at all (soft suspension; account stays active, message shown)
  flow-mfa-required       must pass MFA at every login and is forced to enrol one (TOTP / passkey / email code) if none
                          (everyone else: MFA is asked only if they have a device)
  flow-session-8h         session ends after 8 h, no "remember me" (shared devices / contractors)
  flow-recovery-allowed   may use "Forgot password" (email reset); others are denied at that step
  flow-self-service       may change their own password and profile in the user portal
  flow-invited            group new accounts land in when created through the invitation sign-up flow
  flow-explicit-consent   must approve each application's access (consent screen, re-asked every 4 weeks)

Flows touched/created:
  default-authentication-flow   + deny(blocked) + optional-MFA / required-MFA split + 8h login stage
  default-recovery-flow         + deny unless flow-recovery-allowed
  password-change + user-settings flows  + deny unless flow-self-service
  heardos-enrollment-invitation NEW: invitation -> details -> account created inactive -> activation email -> login
  heardos-provider-authorization NEW: consent only for flow-explicit-consent; set on every provider
  heardos-authenticator-email-setup NEW: lets users enrol an email code as a second factor
"""
import os, sys, json, urllib.request

A = os.environ.get("AUTHENTIK_URL", "https://auth.example.com") + "/api/v3"
H = {"Authorization": "Bearer " + os.environ["AUTHENTIK_TOKEN"], "Content-Type": "application/json"}
DRY = "--dry-run" in sys.argv


def call(m, p, d=None):
    r = urllib.request.Request(A + p, method=m, data=json.dumps(d).encode() if d is not None else None, headers=H)
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            b = x.read(); return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{m} {p} -> {e.code}: {e.read().decode()[:400]}")


def results(p):
    return call("GET", p)["results"]


def find(p, **match):
    for o in results(p + ("&" if "?" in p else "?") + "page_size=200"):
        if all(o.get(k) == v for k, v in match.items()): return o
    return None


def ensure(p, match, body, patch_fields=None):
    """find by `match`; create with `body` if missing; otherwise PATCH `patch_fields` (or all of body) to re-assert."""
    o = find(p, **match)
    if o:
        upd = {k: body[k] for k in (patch_fields or body) if k in body and o.get(k) != body[k]}
        if upd and not DRY: o = call("PATCH", f"{p}{o['pk']}/", upd)
        print(f"  = {p.split('/')[-2]} {match} {'(updated '+','.join(upd)+')' if upd else ''}")
        return o
    print(f"  + {p.split('/')[-2]} {match}")
    return {**body, "pk": f"dry:{list(match.values())[0]}"} if DRY else call("POST", p, body)


# ---------- groups ----------
print("== groups")
GROUPS = {
    "flow-login-blocked": "Members cannot sign in (soft suspension). Shown: account suspended.",
    "flow-mfa-required": "Members must pass MFA at every login and are forced to enrol if they have no device.",
    "flow-session-8h": "Members get an 8-hour session with no remember-me.",
    "flow-recovery-allowed": "Members may reset their password by email (Forgot password).",
    "flow-self-service": "Members may change their own password and profile.",
    "flow-invited": "Accounts created through the invitation sign-up flow.",
    "flow-explicit-consent": "Members must approve each application's access (re-asked every 4 weeks).",
}
old = find("/core/groups/", name="mfa-required")
if old and not find("/core/groups/", name="flow-mfa-required"):
    print("  ~ renaming group mfa-required -> flow-mfa-required (members kept)")
    if not DRY: call("PATCH", f"/core/groups/{old['pk']}/", {"name": "flow-mfa-required"})
G = {}
for name, doc in GROUPS.items():
    G[name] = ensure("/core/groups/", {"name": name}, {"name": name, "is_superuser": False, "attributes": {"heardos.flow": doc}}, ["attributes"])
users = {u["username"]: u for u in results("/core/users/?page_size=100") if u["type"] == "internal"}
def add_members(group, names):
    g = find("/core/groups/", name=group)
    if not g: print(f"  (dry) would add {names} -> {group}"); return
    have = {u["username"] for u in (g.get("users_obj") or [])}
    for n in names:
        if n in users and n not in have and not DRY:
            call("POST", f"/core/groups/{g['pk']}/add_user/", {"pk": users[n]["pk"]}); print(f"  + {n} -> {group}")
add_members("flow-recovery-allowed", list(users))     # everyone can reset today; remove people to revoke
add_members("flow-self-service", list(users))

# ---------- stages ----------
print("== stages")
S = lambda p, name, body: ensure(p, {"name": name}, {"name": name, **body})
deny_blocked = S("/stages/deny/", "heardos-deny-blocked", {"deny_message": "This account is suspended. Please contact an administrator."})
deny_recovery = S("/stages/deny/", "heardos-deny-no-recovery", {"deny_message": "Password reset by email is not enabled for this account. Please contact an administrator."})
deny_selfsvc = S("/stages/deny/", "heardos-deny-no-self-service", {"deny_message": "Self-service changes are not enabled for this account. Please contact an administrator."})
totp = find("/stages/authenticator/totp/", name="default-authenticator-totp-setup")
webauthn = find("/stages/authenticator/webauthn/", name="default-authenticator-webauthn-setup")
email_setup = S("/stages/authenticator/email/", "heardos-authenticator-email-setup",
                {"use_global_settings": True, "subject": "Your HeardOS verification code", "token_expiry": "minutes=10", "friendly_name": "Email code"})
mfa_optional = S("/stages/authenticator/validate/", "default-authentication-mfa-validation",
                 {"not_configured_action": "skip", "device_classes": ["totp", "webauthn", "static", "email"],
                  "configuration_stages": [totp["pk"], webauthn["pk"], email_setup["pk"]], "last_auth_threshold": "seconds=0"})
mfa_required = S("/stages/authenticator/validate/", "heardos-mfa-required",
                 {"not_configured_action": "configure", "device_classes": ["totp", "webauthn", "static", "email"],
                  "configuration_stages": [webauthn["pk"], totp["pk"], email_setup["pk"]], "last_auth_threshold": "seconds=0"})
login_default = find("/stages/user_login/", name="default-authentication-login")
login_8h = S("/stages/user_login/", "heardos-login-8h", {"session_duration": "hours=8", "remember_me_offset": "seconds=0", "terminate_other_sessions": False})
consent = S("/stages/consent/", "heardos-consent-expiring", {"mode": "expiring", "consent_expire_in": "weeks=4"})
invitation = S("/stages/invitation/stages/", "heardos-invitation", {"continue_flow_without_invitation": False})
pw_policy = find("/policies/password/", name="default-password-change-password-policy")
prompts = []
for key, label, typ, order, extra in (
        ("username", "Username", "username", 10, {}), ("name", "Full name", "text", 20, {}),
        ("email", "Email address", "email", 30, {}), ("password", "Password", "password", 40, {"placeholder": "Password"}),
        ("password_repeat", "Repeat password", "password", 50, {"placeholder": "Repeat password"})):
    prompts.append(S("/stages/prompt/prompts/", f"heardos-enroll-{key}", {"field_key": key, "label": label, "type": typ, "required": True, "order": order, **extra}))
enroll_prompt = S("/stages/prompt/stages/", "heardos-enrollment-details", {"fields": [p["pk"] for p in prompts], "validation_policies": [pw_policy["pk"]]})
enroll_write = S("/stages/user_write/", "heardos-enrollment-write",
                 {"create_users_as_inactive": True, "create_users_group": G["flow-invited"]["pk"], "user_creation_mode": "always_create", "user_type": "internal"})
enroll_email = S("/stages/email/", "heardos-enrollment-activation-email",
                 {"use_global_settings": True, "activate_user_on_success": True, "subject": "Activate your HeardOS account",
                  "template": "email/account_confirmation.html", "token_expiry": "minutes=30"})

# ---------- flows ----------
print("== flows")
F = lambda slug, body: ensure("/flows/instances/", {"slug": slug}, {"slug": slug, **body}, ["title", "designation", "authentication", "denied_action"])
auth_flow = find("/flows/instances/", slug="default-authentication-flow")
recovery = find("/flows/instances/", slug="default-recovery-flow")
settings_flow = find("/flows/instances/", slug="default-user-settings-flow")
pw_stage = find("/stages/password/", name="default-authentication-password")
pwchange = call("GET", f"/flows/instances/{find('/flows/instances/', pk=pw_stage['configure_flow'])['slug']}/") if pw_stage.get("configure_flow") else None
enroll_flow = F("heardos-enrollment-invitation", {"name": "HeardOS invitation sign-up", "title": "Create your HeardOS account", "designation": "enrollment",
                                                  "authentication": "require_unauthenticated", "denied_action": "message_continue", "layout": "stacked"})
authz_flow = F("heardos-provider-authorization", {"name": "HeardOS application authorization", "title": "Authorize application", "designation": "authorization",
                                                  "authentication": "require_authenticated", "denied_action": "message_continue", "layout": "stacked"})
email_mfa_flow = F("heardos-authenticator-email-setup", {"name": "HeardOS email code setup", "title": "Set up email verification codes", "designation": "stage_configuration",
                                                         "authentication": "require_authenticated", "denied_action": "message_continue", "layout": "stacked"})
if not DRY:
    call("PATCH", f"/stages/authenticator/email/{email_setup['pk']}/", {"configure_flow": email_mfa_flow["pk"]})
    for st in (totp, webauthn):
        if not st.get("configure_flow"): pass

# ---------- bindings ----------
print("== stage bindings + group policies")
def bind(flow, stage, order, plan, reeval):
    b = find("/flows/bindings/", target=flow["pk"], stage=stage["pk"])
    body = {"target": flow["pk"], "stage": stage["pk"], "order": order, "evaluate_on_plan": plan, "re_evaluate_policies": reeval, "policy_engine_mode": "all"}   # "all": every bound policy/group must pass (default "any" let the passkey-skip policy bypass the group)
    if b:
        upd = {k: v for k, v in body.items() if b.get(k) != v}
        if upd and not DRY: call("PATCH", f"/flows/bindings/{b['pk']}/", upd)
        return b
    print(f"  + bind {stage['name']} @ {order} in {flow['slug']}")
    return {**body, "pk": f"dry:{stage['name']}", "stage_obj": {"name": stage["name"]}} if DRY else call("POST", "/flows/bindings/", body)

_PB = None
def all_pbs(refresh=False):
    global _PB
    if _PB is None or refresh: _PB = results("/policies/bindings/?page_size=1000")
    return _PB
def pbs_for(binding):
    return [] if str(binding["pk"]).startswith("dry:") else [pb for pb in all_pbs() if pb["target"] == binding["pk"]]

def group_policy(binding, group, negate):
    """bind a group directly to a stage binding (no policy object needed); negate = applies to NON-members"""
    for pb in pbs_for(binding):
        if pb.get("group") == group["pk"]:
            if pb.get("negate") != negate and not DRY: call("PATCH", f"/policies/bindings/{pb['pk']}/", {"negate": negate})
            return pb
    print(f"  + group {group['name']}{' (negated)' if negate else ''} on {binding.get('stage_obj', {}).get('name', '?')}")
    if DRY: return None
    r = call("POST", "/policies/bindings/", {"target": binding["pk"], "group": group["pk"], "order": 0, "negate": negate, "enabled": True, "failure_result": False, "timeout": 30}); all_pbs(True); return r

def drop_policy_binding(binding, policy_name):
    for pb in pbs_for(binding):
        if (pb.get("policy_obj") or {}).get("name") == policy_name and not DRY:
            call("DELETE", f"/policies/bindings/{pb['pk']}/"); all_pbs(True); print(f"  - removed policy {policy_name} from {binding['stage_obj']['name']}")

# login flow
b = bind(auth_flow, deny_blocked, 15, False, True);      group_policy(b, G["flow-login-blocked"], False)
b = bind(auth_flow, mfa_optional, 30, False, True);      drop_policy_binding(b, "mfa-required-only"); group_policy(b, G["flow-mfa-required"], True)
b = bind(auth_flow, mfa_required, 31, False, True);      group_policy(b, G["flow-mfa-required"], False)
skip_pwl = find("/policies/expression/", name="default-authentication-flow-authenticator-validate-stage")
if skip_pwl and not DRY and not any((pb.get("policy") == skip_pwl["pk"]) for pb in pbs_for(b)):
    call("POST", "/policies/bindings/", {"target": b["pk"], "policy": skip_pwl["pk"], "order": 1, "enabled": True, "negate": False, "failure_result": False, "timeout": 30})
b = bind(auth_flow, login_8h, 99, False, True);          group_policy(b, G["flow-session-8h"], False)
b = bind(auth_flow, login_default, 100, False, True);    group_policy(b, G["flow-session-8h"], True)
# recovery
b = bind(recovery, deny_recovery, 15, False, True);      group_policy(b, G["flow-recovery-allowed"], True)
# self-service
b = bind(settings_flow, deny_selfsvc, 10, True, False);  group_policy(b, G["flow-self-service"], True)
if pwchange:
    b = bind(pwchange, deny_selfsvc, 5, True, False);    group_policy(b, G["flow-self-service"], True)
# enrollment by invitation
bind(enroll_flow, invitation, 0, True, False); bind(enroll_flow, enroll_prompt, 10, True, False)
bind(enroll_flow, enroll_write, 20, True, False); bind(enroll_flow, enroll_email, 30, True, False); bind(enroll_flow, login_default, 40, True, False)
# authorization with per-group consent
b = bind(authz_flow, consent, 0, True, False);           group_policy(b, G["flow-explicit-consent"], False)
# email-code MFA enrolment flow
bind(email_mfa_flow, email_setup, 0, True, False)

# ---------- providers use the new authorization flow ----------
print("== providers -> heardos-provider-authorization")
for kind in ("oauth2", "proxy"):
    for p in results(f"/providers/{kind}/?page_size=100"):
        if p.get("authorization_flow") != authz_flow["pk"]:
            print(f"  ~ {kind} {p['name']}")
            if not DRY: call("PATCH", f"/providers/{kind}/{p['pk']}/", {"authorization_flow": authz_flow["pk"]})

# ---------- retire the old expression policies ----------
for name in ("mfa-required-only", "mfa-required-for-admins"):
    pol = find("/policies/expression/", name=name)
    if pol and pol.get("bound_to", 0) == 0 and not DRY:
        call("DELETE", f"/policies/expression/{pol['pk']}/"); print(f"  - deleted unused policy {name}")
print("done" + (" (dry run)" if DRY else ""))
