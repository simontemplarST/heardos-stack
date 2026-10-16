#!/usr/bin/env python3
"""42-ak-app.py — register an application in Authentik the HeardOS way: provider + application + group app-<slug> + binding.
  oidc  SLUG --name "Paperless" --launch https://paperless.DOMAIN/ --redirect URI [--redirect URI...] [--public] [--users a,b] [--out FILE]
  proxy SLUG --name "Baikal admin" --host https://dav.DOMAIN [--skip REGEX ...] [--users a,b]
  user  add USERNAME --email E --name N [--password P]      create a family member
  user  access USERNAME SLUG[,SLUG...]                      add them to those apps' groups
  user  list | join/leave USERNAME GROUP[,..] | welcome USERNAME | suspend/resume USERNAME
  user  revoke USERNAME SLUG[,SLUG...]
oidc prints CLIENT_ID/CLIENT_SECRET (and appends them to --out as AUTHENTIK_<SLUG>_CLIENT_ID/SECRET). Idempotent: existing objects are reused."""
import argparse, os, secrets, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ak import AK, load_env

env = load_env(); ak = AK(env)
FLOW_AUTHZ = "default-provider-authorization-implicit-consent"; FLOW_INVAL = "default-provider-invalidation-flow"

def all_internal_users(): return [u["pk"] for u in ak.results("/core/users/?type=internal&is_active=true") if u["username"] != "akadmin"]
def users_arg(s): return [ak.user(x.strip())["pk"] for x in s.split(",")] if s else all_internal_users()
def ensure_app(slug, name, provider_pk, launch, users):
    app = ak.one("/core/applications/", slug=slug) or ak.post("/core/applications/", {"name": name, "slug": slug, "provider": provider_pk, "meta_launch_url": launch or "", "open_in_new_tab": False, "policy_engine_mode": "any"})
    if app.get("provider") != provider_pk: ak.patch(f"/core/applications/{slug}/", {"provider": provider_pk})
    g = ak.ensure_group(f"app-{slug}", users, f"Access to application '{name}'. Membership = access.")
    ak.ensure_binding(app["pk"], group=g["pk"])
    return app, g

def cmd_oidc(a):
    mappings = [m["pk"] for m in ak.results("/propertymappings/all/") if m["name"] in ("authentik default OAuth Mapping: OpenID 'openid'", "authentik default OAuth Mapping: OpenID 'profile'", "heardos-email-verified")]
    key = next((k["pk"] for k in ak.results("/crypto/certificatekeypairs/?has_key=true") if "authentik Self-signed" in k["name"]), None)
    prov = ak.one("/providers/oauth2/", name=a.slug)
    cid = csec = None
    if not prov:
        cid = secrets.token_urlsafe(30).replace("-", "").replace("_", "")[:40]; csec = secrets.token_urlsafe(96).replace("-", "").replace("_", "")[:128]
        prov = ak.post("/providers/oauth2/", {"name": a.slug, "authorization_flow": ak.flow(FLOW_AUTHZ)["pk"], "invalidation_flow": ak.flow(FLOW_INVAL)["pk"],
            "client_type": "public" if a.public else "confidential", "client_id": cid, "client_secret": csec, "signing_key": key, "property_mappings": mappings,
            "sub_mode": "hashed_user_id", "include_claims_in_id_token": True, "access_code_validity": "minutes=1", "access_token_validity": "hours=1", "refresh_token_validity": "days=30",
            "redirect_uris": [{"matching_mode": "strict", "url": u} for u in a.redirect]})
        ak.patch(f"/providers/oauth2/{prov['pk']}/", {"grant_types": ["authorization_code", "refresh_token"]})  # API default is [] -> login breaks
    else:
        have = [r["url"] for r in prov["redirect_uris"]]; new = [u for u in a.redirect if u not in have]
        if new: ak.patch(f"/providers/oauth2/{prov['pk']}/", {"redirect_uris": prov["redirect_uris"] + [{"matching_mode": "strict", "url": u} for u in new]})
        if not prov.get("grant_types"): ak.patch(f"/providers/oauth2/{prov['pk']}/", {"grant_types": ["authorization_code", "refresh_token"]})
        if a.public and prov.get("client_type") != "public": ak.patch(f"/providers/oauth2/{prov['pk']}/", {"client_type": "public"})
    app, g = ensure_app(a.slug, a.name or a.slug, prov["pk"], a.launch, users_arg(a.users))
    issuer = f"https://auth.{env['DOMAIN']}/application/o/{a.slug}/"
    print(f"oidc app {a.slug}: issuer {issuer}\n  group {g['name']} members {len(g.get('users', []))}")
    if cid:
        print(f"  CLIENT_ID={cid}\n  CLIENT_SECRET={csec}")
        if a.out:
            with open(a.out, "a") as f: f.write(f"AUTHENTIK_{a.slug.upper().replace('-', '_')}_CLIENT_ID={cid}\nAUTHENTIK_{a.slug.upper().replace('-', '_')}_CLIENT_SECRET={csec}\n")
            print(f"  appended to {a.out}")
    else: print("  provider existed; client secret unchanged")

def cmd_proxy(a):
    prov = ak.one("/providers/proxy/", name=a.slug)
    # exact list, NOT a prefix match: Authentik 2026.x also ships 'bound_key' (needs DPoP -> authorize fails with
    # "dpop_jkt is required when bound_key scope is requested"), DCR and API-access mappings that must not be on a proxy provider
    want = ("authentik default OAuth Mapping: Proxy outpost", "authentik default OAuth Mapping: OpenID 'openid'", "authentik default OAuth Mapping: OpenID 'email'",
            "authentik default OAuth Mapping: OpenID 'profile'", "authentik default OAuth Mapping: Application Entitlements")
    mappings = [m["pk"] for m in ak.results("/propertymappings/all/") if m["name"] in want]
    body = {"name": a.slug, "authorization_flow": ak.flow(FLOW_AUTHZ)["pk"], "invalidation_flow": ak.flow(FLOW_INVAL)["pk"], "property_mappings": mappings,
            "external_host": a.host, "mode": "forward_single", "skip_path_regex": "\n".join(a.skip or []), "intercept_header_auth": True,
            "access_token_validity": "hours=24", "refresh_token_validity": "days=30"}
    prov = ak.patch(f"/providers/proxy/{prov['pk']}/", {"mode": "forward_single", "skip_path_regex": body["skip_path_regex"], "external_host": a.host}) if prov else ak.post("/providers/proxy/", body)
    o = ak.embedded_outpost(); provs = sorted(set(o["providers"] + [prov["pk"]]))
    if provs != sorted(o["providers"]): ak.patch(f"/outposts/instances/{o['pk']}/", {"providers": provs})
    app, g = ensure_app(a.slug, a.name or a.slug, prov["pk"], a.launch or a.host + "/", users_arg(a.users))
    print(f"proxy app {a.slug}: external host {a.host}, skip {a.skip or '-'}; on the embedded outpost; group {g['name']}")

def cmd_user(a):
    if a.action == "add":
        u = ak.user(a.username)
        if not u:
            u = ak.post("/core/users/", {"username": a.username, "name": a.name or a.username, "email": a.email, "is_active": True, "type": "internal"})
            pw = a.password or secrets.token_urlsafe(16) + "!"
            ak.post(f"/core/users/{u['pk']}/set_password/", {"password": pw}); print(f"user {a.username} created, password: {pw}")
        else: print(f"user {a.username} exists")
    elif a.action == "list":   # JSON for the installer's People screen
        import json
        groups = {g["pk"]: g["name"] for g in ak.results("/core/groups/")}
        rows = []
        for u in ak.results("/core/users/?type=internal"):
            if u["username"] == "akadmin":
                continue
            names = [groups.get(g, "") for g in u.get("groups", [])]
            rows.append({"username": u["username"], "name": u["name"], "email": u["email"], "active": u["is_active"],
                         "apps": sorted(n[4:] for n in names if n.startswith("app-")),
                         "flows": sorted(n for n in names if n.startswith("flow-")), "admin": "authentik Admins" in names,
                         "last_login": u.get("last_login")})
        print("HEARDOS_JSON " + json.dumps(rows))
    elif a.action in ("join", "leave"):   # any group by name (flow-* rules, authentik Admins)
        u = ak.user(a.username) or sys.exit(f"no such user {a.username}")
        for name in a.slugs.split(","):
            g = ak.group(name.strip()) or sys.exit(f"no group {name}")
            ak.post(f"/core/groups/{g['pk']}/{'add_user' if a.action == 'join' else 'remove_user'}/", {"pk": u["pk"]})
            print(f"{a.username} {'joined' if a.action == 'join' else 'left'} {g['name']}")
    elif a.action == "welcome":   # email a set-your-password link (needs mail + the recovery flow from recovery-flow.py)
        u = ak.user(a.username) or sys.exit(f"no such user {a.username}")
        st = ak.one("/stages/email/", name="default-recovery-email") or sys.exit("no recovery email stage; run authentik/recovery-flow.py")
        ak.call("POST", f"/core/users/{u['pk']}/recovery_email/?email_stage={st['pk']}")
        print(f"set-password email sent to {u['email']}")
    elif a.action in ("suspend", "resume"):
        u = ak.user(a.username) or sys.exit(f"no such user {a.username}")
        ak.patch(f"/core/users/{u['pk']}/", {"is_active": a.action == "resume"}); print(f"{a.username} {a.action}d")
    else:
        u = ak.user(a.username) or sys.exit(f"no such user {a.username}")
        for slug in a.slugs.split(","):
            g = ak.group(f"app-{slug.strip()}") or sys.exit(f"no group app-{slug}")
            if a.action == "access": ak.post(f"/core/groups/{g['pk']}/add_user/", {"pk": u["pk"]}); print(f"{a.username} -> {g['name']}")
            else: ak.post(f"/core/groups/{g['pk']}/remove_user/", {"pk": u["pk"]}); print(f"{a.username} removed from {g['name']}")

p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
o = sub.add_parser("oidc"); o.add_argument("slug"); o.add_argument("--name"); o.add_argument("--launch"); o.add_argument("--redirect", action="append", required=True); o.add_argument("--users"); o.add_argument("--out"); o.add_argument("--public", action="store_true", help="public PKCE client (needed when a native app does the code exchange itself, e.g. Swift Paperless); the web app may still send its secret"); o.set_defaults(f=cmd_oidc)
x = sub.add_parser("proxy"); x.add_argument("slug"); x.add_argument("--name"); x.add_argument("--host", required=True); x.add_argument("--skip", action="append"); x.add_argument("--launch"); x.add_argument("--users"); x.set_defaults(f=cmd_proxy)
u = sub.add_parser("user"); u.add_argument("action", choices=["add", "access", "revoke", "list", "join", "leave", "welcome", "suspend", "resume"]); u.add_argument("username", nargs="?", default=""); u.add_argument("slugs", nargs="?"); u.add_argument("--email"); u.add_argument("--name"); u.add_argument("--password"); u.set_defaults(f=cmd_user)
a = p.parse_args(); a.f(a)
