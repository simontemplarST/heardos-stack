#!/usr/bin/env python3
"""Build Authentik's password-recovery flow ("Forgot password?") and wire it in. Idempotent.
  flow  default-recovery-flow (designation recovery)
    10  default-recovery-identification   Identification stage (email + username, no password stage)
    20  default-recovery-email            Email stage: sends a one-time link, token valid 30 min, uses the global SMTP config
    30  default-recovery-user-write       (no-op for recovery; keeps the pending user)   -- skipped, see below
    30  default-recovery-password-prompt  Prompt stage: password + password_repeat
    40  default-recovery-user-write       User Write stage (saves the new password)
    50  default-recovery-user-login       logs the user in afterwards
  brand authentik-default: flow_recovery = this flow
  default-authentication-identification: recovery_flow = this flow (adds the "Forgot password?" link)
Requires AUTHENTIK_TOKEN in env."""
import os, json, urllib.request
A = "http://192.168.10.20:9000/api/v3"; H = {"Authorization": "Bearer " + os.environ["AUTHENTIK_TOKEN"], "Content-Type": "application/json"}
def call(m, p, d=None):
    r = urllib.request.Request(A + p, method=m, data=json.dumps(d).encode() if d is not None else None, headers=H)
    try:
        with urllib.request.urlopen(r, timeout=60) as x: return json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e: raise SystemExit("%s %s -> %s: %s" % (m, p, e.code, e.read().decode()[:400]))
def one(path, **q):
    res = call("GET", path + "?" + "&".join("%s=%s" % kv for kv in q.items()))["results"]
    return res[0] if res else None

flow = one("/flows/instances/", slug="default-recovery-flow") or call("POST", "/flows/instances/", {
    "name": "Password recovery", "slug": "default-recovery-flow", "title": "Reset your HeardOS password", "designation": "recovery",
    "authentication": "require_unauthenticated", "denied_action": "message_continue", "policy_engine_mode": "any", "compatibility_mode": False, "layout": "stacked"})
print("flow:", flow["slug"], flow["pk"])

ident = one("/stages/identification/", name="default-recovery-identification") or call("POST", "/stages/identification/", {
    "name": "default-recovery-identification", "user_fields": ["email", "username"], "case_insensitive_matching": True, "show_matched_user": False,
    "pretend_user_exists": True, "password_stage": None, "sources": []})
email = one("/stages/email/", name="default-recovery-email") or call("POST", "/stages/email/", {
    "name": "default-recovery-email", "use_global_settings": True, "token_expiry": "minutes=30", "subject": "Reset your HeardOS password",
    "template": "email/password_reset.html", "activate_user_on_success": True})
# password prompts
def prompt(field_key, label, order, placeholder=""):
    p = one("/stages/prompt/prompts/", field_key=field_key, name="default-recovery-" + field_key)
    return p or call("POST", "/stages/prompt/prompts/", {"name": "default-recovery-" + field_key, "field_key": field_key, "label": label, "type": "password",
        "required": True, "placeholder": placeholder, "order": order, "placeholder_expression": False})
p1 = prompt("password", "New password", 0); p2 = prompt("password_repeat", "Repeat the new password", 1)
pwpolicy = one("/policies/password/", name="default-password-change-password-policy")
pstage = one("/stages/prompt/stages/", name="default-recovery-password-prompt") or call("POST", "/stages/prompt/stages/", {
    "name": "default-recovery-password-prompt", "fields": [p1["pk"], p2["pk"]], "validation_policies": [pwpolicy["pk"]] if pwpolicy else []})
uwrite = one("/stages/user_write/", name="default-recovery-user-write") or call("POST", "/stages/user_write/", {
    "name": "default-recovery-user-write", "user_creation_mode": "never_create", "create_users_as_inactive": False})
login = one("/stages/user_login/", name="default-recovery-user-login") or call("POST", "/stages/user_login/", {"name": "default-recovery-user-login", "session_duration": "seconds=0"})

bound = {b["stage"]: b for b in call("GET", "/flows/bindings/?page_size=200")["results"] if b["target"] == flow["pk"]}
for order, st in ((10, ident), (20, email), (30, pstage), (40, uwrite), (50, login)):
    if st["pk"] not in bound:
        call("POST", "/flows/bindings/", {"target": flow["pk"], "stage": st["pk"], "order": order, "evaluate_on_plan": True, "re_evaluate_policies": False, "policy_engine_mode": "any"}); print("bound", st["name"], "@", order)
    else: print("already bound", st["name"])

brand = [b for b in call("GET", "/core/brands/")["results"] if b["domain"] == "authentik-default"][0]
if brand.get("flow_recovery") != flow["pk"]: call("PATCH", "/core/brands/%s/" % brand["brand_uuid"], {"flow_recovery": flow["pk"]}); print("brand flow_recovery set")
auth_ident = one("/stages/identification/", name="default-authentication-identification")
if auth_ident.get("recovery_flow") != flow["pk"]: call("PATCH", "/stages/identification/%s/" % auth_ident["pk"], {"recovery_flow": flow["pk"]}); print("login page now shows 'Forgot password?'")
print("done; recovery URL: https://auth.example.com/if/flow/default-recovery-flow/")
