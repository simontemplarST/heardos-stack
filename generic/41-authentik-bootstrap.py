#!/usr/bin/env python3
"""41-authentik-bootstrap.py — run from the laptop (or the host) after 40-authentik-stack.sh. Idempotent.
Sets up everything apps rely on:
  - brand title, email-verified scope mapping (apps like Mealie require email_verified)
  - the admin user from the plan file (ADMIN_USER/ADMIN_EMAIL) in 'authentik Admins', with a generated password
  - brute-force protection: reputation policy bound NEGATED to the authentication flow
  - MFA forced for members of 'authentik Admins': they skip the optional validation stage (order 30) and get
    `heardos-mfa-required` (order 31, enrol if missing, no email codes); everyone else is asked only if they have a device
  - no self-enrolment, no recovery flow on the brand
The generated admin password goes to $HEARDOS_ADMIN_PW_FILE, or else a HEARDOS_OUTPUT line (never printed in clear)."""
import secrets, string, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ak import AK, load_env

env = load_env(); ak = AK(env)
print("authentik", ak.get("/admin/version/").get("version_current"))

# 1. brand
brand = ak.results("/core/brands/")[0]
ak.patch(f"/core/brands/{brand['brand_uuid']}/", {"branding_title": env.get("BRAND_TITLE") or "HeardOS", "flow_recovery": None})
print("brand: title set, recovery flow off")

# 2. verified-email scope mapping. Accounts are created by an admin (or by invitation, which proves the address with an
# activation link), so the address counts as verified -- unless the user attribute email_verified is set to false, which
# an admin should do for anyone whose address was changed through self-service (the user-settings flow lets
# flow-self-service members edit their email and Authentik does not re-verify it). Apps that link accounts by email trust this.
EV_DESC = "Email address (verified unless the user attribute email_verified is false)"
EV_EXPR = "return {\"email\": request.user.email, \"email_verified\": bool(request.user.attributes.get(\"email_verified\", True))}"
m = ak.mapping("heardos-email-verified")
if not m:
    m = ak.post("/propertymappings/provider/scope/", {"name": "heardos-email-verified", "scope_name": "email",
        "description": EV_DESC, "expression": EV_EXPR})
elif m.get("expression") != EV_EXPR:
    ak.patch(f"/propertymappings/provider/scope/{m['pk']}/", {"expression": EV_EXPR, "description": EV_DESC})
print("scope mapping heardos-email-verified:", m["pk"])

# 3. admin user
admins = ak.group("authentik Admins"); u = ak.user(env["ADMIN_USER"])
if not u:
    u = ak.post("/core/users/", {"username": env["ADMIN_USER"], "name": env.get("ADMIN_NAME", env["ADMIN_USER"]), "email": env["ADMIN_EMAIL"],
                                  "is_active": True, "type": "internal", "groups": [admins["pk"]]})
    pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20)) + "!"
    ak.post(f"/core/users/{u['pk']}/set_password/", {"password": pw})
    pw_file = os.environ.get("HEARDOS_ADMIN_PW_FILE")
    if pw_file:   # the installer collects it into its vault; never echo it
        fd = os.open(pw_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, pw.encode()); os.close(fd)
        print(f"admin user {env['ADMIN_USER']} created; password written to {pw_file}")
        print(f"HEARDOS_OUTPUT ADMIN_PASSWORD_FILE={pw_file}")
    else:   # no file: a HEARDOS_OUTPUT line (masked by the installer); never a clear-text print
        print(f"admin user {env['ADMIN_USER']} created; password follows as HEARDOS_OUTPUT ADMIN_PASSWORD (store it now)")
        print(f"HEARDOS_OUTPUT ADMIN_PASSWORD={pw}")
else:
    if admins["pk"] not in [g["pk"] for g in u.get("groups_obj", [])]:
        ak.post(f"/core/groups/{admins['pk']}/add_user/", {"pk": u["pk"]})
    print(f"admin user {env['ADMIN_USER']} exists")
mfa_grp = ak.group("flow-mfa-required")   # exists once authentik/business-flows.py has run; admins are covered by the policy anyway
if mfa_grp:
    ak.post(f"/core/groups/{mfa_grp['pk']}/add_user/", {"pk": u["pk"]})

# 4. brute force: reputation policy, NEGATED on the auth flow (the policy passes for bad scores)
auth = ak.flow("default-authentication-flow")
rep = ak.one("/policies/reputation/", name="brute-force-reputation") or ak.post("/policies/reputation/", {"name": "brute-force-reputation", "check_ip": True, "check_username": True, "threshold": -5})
b = ak.ensure_binding(auth["policybindingmodel_ptr_id"], policy=rep["pk"], negate=True)
if not b.get("negate"):
    ak.put(f"/policies/bindings/{b['pk']}/", {"target": b["target"], "policy": rep["pk"], "negate": True, "enabled": True, "order": 0, "timeout": 30, "failure_result": False})
print("reputation policy bound (negated) to the authentication flow")

# 5. MFA for admins. Same layout as authentik/business-flows.py (which adds the flow-mfa-required group and email codes
# for ordinary users); both scripts agree: order 30 = optional ("skip"), order 31 = required ("configure", no email).
val = ak.one("/stages/authenticator/validate/", name="default-authentication-mfa-validation")
cfg = [s["pk"] for s in ak.results("/stages/all/") if s["name"] in ("default-authenticator-totp-setup", "default-authenticator-webauthn-setup")]
ak.patch(f"/stages/authenticator/validate/{val['pk']}/", {"not_configured_action": "skip",
    "device_classes": sorted(set(val.get("device_classes") or []) | {"totp", "webauthn", "static"}),
    "configuration_stages": sorted(set(val.get("configuration_stages") or []) | set(cfg)), "last_auth_threshold": "seconds=0"})
pol = ak.one("/policies/expression/", name="mfa-required-for-admins") or ak.post("/policies/expression/", {"name": "mfa-required-for-admins",
    "expression": "user = request.context.get(\"pending_user\") or request.user\nif not getattr(user, \"is_authenticated\", False) and not request.context.get(\"pending_user\"):\n    return False\nreturn ak_is_group_member(user, name=\"authentik Admins\")\n"})
sb = next(x for x in ak.results(f"/flows/bindings/?target={auth['pk']}") if x["order"] == 30)
# order 30 (optional): policy_engine_mode MUST be "all": the stock "skip after passwordless WebAuthn" policy on this
# binding is True for everyone, and in "any" mode it alone sends every user into the stage. Admins are excluded here
# (policy bound NEGATED) so they always reach the required stage at order 31.
ak.patch(f"/flows/bindings/{sb['pk']}/", {"evaluate_on_plan": False, "re_evaluate_policies": True, "policy_engine_mode": "all"})
b30 = ak.ensure_binding(sb["policybindingmodel_ptr_id"], policy=pol["pk"], negate=True)
if not b30.get("negate"): ak.patch(f"/policies/bindings/{b30['pk']}/", {"negate": True})
req = ak.one("/stages/authenticator/validate/", name="heardos-mfa-required")
req_body = {"name": "heardos-mfa-required", "not_configured_action": "configure", "device_classes": ["totp", "webauthn", "static"],
            "configuration_stages": cfg, "last_auth_threshold": "seconds=0"}
req = ak.patch(f"/stages/authenticator/validate/{req['pk']}/", {k: v for k, v in req_body.items() if k != "configuration_stages"}) if req else ak.post("/stages/authenticator/validate/", req_body)
rb = next((x for x in ak.results(f"/flows/bindings/?target={auth['pk']}") if x["stage"] == req["pk"]), None)
rb_body = {"target": auth["pk"], "stage": req["pk"], "order": 31, "evaluate_on_plan": False, "re_evaluate_policies": True, "policy_engine_mode": "any"}
rb = ak.patch(f"/flows/bindings/{rb['pk']}/", rb_body) if rb else ak.post("/flows/bindings/", rb_body)
# order 31 (required): "any" = admin policy OR the flow-mfa-required group (business-flows.py binds the group)
b31 = ak.ensure_binding(rb["policybindingmodel_ptr_id"], policy=pol["pk"])
if b31.get("negate"): ak.patch(f"/policies/bindings/{b31['pk']}/", {"negate": False})
for pb in ak.results(f"/policies/bindings/?target={rb['policybindingmodel_ptr_id']}"):   # in "any" mode the passkey-skip policy
    if (pb.get("policy_obj") or {}).get("name") == "default-authentication-flow-authenticator-validate-stage":   # would let everyone in
        ak.delete(f"/policies/bindings/{pb['pk']}/")
print("MFA enforced for 'authentik Admins' (heardos-mfa-required at order 31; email codes not accepted)")

# 5b. passkeys: enrolled WebAuthn credentials are discoverable + user-verified, and a passwordless flow is offered on the login page
wa = ak.one("/stages/authenticator/webauthn/", name="default-authenticator-webauthn-setup")
ak.patch(f"/stages/authenticator/webauthn/{wa['pk']}/", {"resident_key_requirement": "required", "user_verification": "required", "friendly_name": "Passkey"})
ak.patch("/flows/instances/default-authenticator-webauthn-setup/", {"title": "Set up a passkey"})   # flow endpoints are keyed by slug
pkv = ak.one("/stages/authenticator/validate/", name="heardos-passkey-validation") or ak.post("/stages/authenticator/validate/", {"name": "heardos-passkey-validation",
    "device_classes": ["webauthn"], "not_configured_action": "deny", "webauthn_user_verification": "required", "last_auth_threshold": "seconds=0", "configuration_stages": []})
pwl = ak.one("/flows/instances/", slug="heardos-passwordless") or ak.post("/flows/instances/", {"name": "HeardOS passwordless", "slug": "heardos-passwordless",
    "title": "Sign in with a passkey", "designation": "authentication", "authentication": "require_unauthenticated", "layout": "stacked", "denied_action": "message_continue"})
login = ak.one("/stages/user_login/", name="default-authentication-login")
have = {b["order"]: b for b in ak.results(f"/flows/bindings/?target={pwl['pk']}")}
for order, stage in ((10, pkv["pk"]), (20, login["pk"])):
    body = {"target": pwl["pk"], "stage": stage, "order": order, "evaluate_on_plan": False, "re_evaluate_policies": True}
    ak.patch(f"/flows/bindings/{have[order]['pk']}/", body) if order in have else ak.post("/flows/bindings/", body)
ident = ak.one("/stages/identification/", name="default-authentication-identification")
ak.patch(f"/stages/identification/{ident['pk']}/", {"user_fields": ident["user_fields"], "sources": ident["sources"], "passwordless_flow": pwl["pk"]})  # PATCH must resend user_fields/sources
print("passkeys: 'Use a security key' on the login page -> flow heardos-passwordless")

# 6. sanity: anonymous flow start must show the identification stage
import urllib.request, json
chk = json.load(urllib.request.urlopen(env["AUTHENTIK_URL"].rstrip("/") + "/api/v3/flows/executor/default-authentication-flow/?query="))
print("login flow check:", chk.get("component"), chk.get("error_message") or "OK")
if chk.get("component") != "ak-stage-identification": raise SystemExit("login flow is denying anonymous users; inspect the flow bindings before continuing")
