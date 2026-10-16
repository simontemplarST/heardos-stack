#!/usr/bin/env python3
"""test-flows.py — prove the group-driven flows with a throwaway user, then delete it.
Creates user `flowtest` (password set via API), drives the login and recovery flows through the flow-executor API
for each group state, prints the stage reached, and removes the user at the end. Needs AUTHENTIK_TOKEN."""
import os, json, urllib.request, http.cookiejar, secrets
A = os.environ.get("AUTHENTIK_URL", "https://auth.example.com") + "/api/v3"
H = {"Authorization": "Bearer " + os.environ["AUTHENTIK_TOKEN"], "Content-Type": "application/json"}
def api(m, p, d=None):
    r = urllib.request.Request(A + p, method=m, data=json.dumps(d).encode() if d is not None else None, headers=H)
    with urllib.request.urlopen(r, timeout=60) as x: b = x.read(); return json.loads(b) if b else {}
def group(name): return [g for g in api("GET", "/core/groups/?page_size=200")["results"] if g["name"] == name][0]
def run_flow(slug, steps):
    cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    def f(m, d=None):
        r = urllib.request.Request(f"{A}/flows/executor/{slug}/?query=", method=m, data=json.dumps(d).encode() if d else None, headers={"Content-Type": "application/json"})
        with op.open(r, timeout=60) as x: return json.load(x)
    out = f("GET")
    for s in steps:
        if out.get("component") != s["component"]: break
        out = f("POST", s)
    return out
pw = secrets.token_urlsafe(18) + "Aa1!"
u = [x for x in api("GET", "/core/users/?username=flowtest")["results"]]
u = u[0] if u else api("POST", "/core/users/", {"username": "flowtest", "name": "Flow Test", "email": "flowtest@example.com", "is_active": True, "type": "internal"})
api("POST", f"/core/users/{u['pk']}/set_password/", {"password": pw})
def set_groups(*names):
    for g in api("GET", "/core/groups/?search=flow-&page_size=50")["results"]:
        if u["pk"] in g.get("users", []): api("POST", f"/core/groups/{g['pk']}/remove_user/", {"pk": u["pk"]})
    for n in names: api("POST", f"/core/groups/{group(n)['pk']}/add_user/", {"pk": u["pk"]})
login = [{"component": "ak-stage-identification", "uid_field": "flowtest"}, {"component": "ak-stage-password", "password": pw}]
def show(label, out): print(f"  {label:48s} -> {out.get('component')} {out.get('error_message') or out.get('response_errors') or ''}")
print("== login flow")
set_groups();                                   show("no groups (expect xak-flow-redirect = logged in)", run_flow("default-authentication-flow", login))
set_groups("flow-login-blocked");               show("flow-login-blocked (expect ak-stage-access-denied)", run_flow("default-authentication-flow", login))
set_groups("flow-mfa-required");                show("flow-mfa-required, no device (expect MFA enrol stage)", run_flow("default-authentication-flow", login))
set_groups("flow-session-8h");                  show("flow-session-8h (expect xak-flow-redirect)", run_flow("default-authentication-flow", login))
print("== recovery flow")
rec = [{"component": "ak-stage-identification", "uid_field": "flowtest"}]
set_groups();                                   show("not in flow-recovery-allowed (expect access-denied)", run_flow("default-recovery-flow", rec))
print("   (positive recovery path not exercised here: it would email flowtest@example.com)")
api("DELETE", f"/core/users/{u['pk']}/"); print("flowtest user deleted")
