#!/usr/bin/env python3
"""42-ak-app.py — register an application in Authentik the HeardOS way: provider + application + group app-<slug> + binding.
  oidc  SLUG --name "Paperless" --launch https://paperless.DOMAIN/ --redirect URI [--redirect URI...] [--public] [--users a,b] [--out FILE]
  proxy SLUG --name "Baikal admin" --host https://dav.DOMAIN [--skip REGEX ...] [--users a,b|all] [--header-auth]
  user  add USERNAME --email E --name N [--password-stdin] [--admin]   create a family member (--admin: authentik Admins
                                                            + flow-mfa-required). Password: $HEARDOS_PASSWORD_FILE or
                                                            stdin; else generated and handed back (file / HEARDOS_OUTPUT)
  user  access USERNAME SLUG[,SLUG...]                      add them to those apps' groups
  user  list | join/leave USERNAME GROUP[,..] | welcome USERNAME | suspend/resume USERNAME
  user  revoke USERNAME SLUG[,SLUG...]
  token rotate [--days 90] [--write-env] [--keep-old]       new API token for the `heardos-automation` service account
  token revoke-bootstrap | status                           delete akadmin's authentik-bootstrap-token / show expiry
oidc appends CLIENT_ID/CLIENT_SECRET to --out as AUTHENTIK_<SLUG>_CLIENT_ID/SECRET (without --out: HEARDOS_OUTPUT lines);
the secret is never printed in clear. Idempotent: existing objects are reused.
--users: oidc default = every internal user; proxy default = ADMIN_USER only (least privilege), `--users all` for everyone."""
import argparse, os, secrets, sys, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ak import AK, load_env, set_env_value, read_secret_input, hand_back_secret

env = load_env(); ak = AK(env)
FLOW_AUTHZ = "default-provider-authorization-implicit-consent"; FLOW_INVAL = "default-provider-invalidation-flow"

def all_internal_users(): return [u["pk"] for u in ak.results("/core/users/?type=internal&is_active=true") if u["username"] != "akadmin"]
def users_arg(s, default="all"):
    s = s or default
    if s == "all": return all_internal_users()
    out = []
    for x in s.split(","):
        u = ak.user(x.strip()) or sys.exit(f"no such user {x.strip()}")
        out.append(u["pk"])
    return out
def ensure_app(slug, name, provider_pk, launch, users):
    app = ak.one("/core/applications/?superuser_full_list=true", slug=slug) or ak.post("/core/applications/", {"name": name, "slug": slug, "provider": provider_pk, "meta_launch_url": launch or "", "open_in_new_tab": False, "policy_engine_mode": "any"})
    if app.get("provider") != provider_pk: ak.patch(f"/core/applications/{slug}/", {"provider": provider_pk})
    g = ak.ensure_group(f"app-{slug}", users, f"Access to application '{name}'. Membership = access.")
    ak.ensure_binding(app["pk"], group=g["pk"])
    return app, g

def cmd_oidc(a):
    mappings = [m["pk"] for m in ak.results("/propertymappings/all/") if m["name"] in ("authentik default OAuth Mapping: OpenID 'openid'", "authentik default OAuth Mapping: OpenID 'profile'", "heardos-email-verified", "HeardOS email (verified)")]
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
        key = f"AUTHENTIK_{a.slug.upper().replace('-', '_')}"
        print(f"  CLIENT_ID={cid}")
        if a.out:   # the secret goes to the file only, never to stdout
            fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as f: f.write(f"{key}_CLIENT_ID={cid}\n{key}_CLIENT_SECRET={csec}\n")
            print(f"  client id + secret appended to {a.out}")
        else:
            print(f"HEARDOS_OUTPUT {key}_CLIENT_ID={cid}\nHEARDOS_OUTPUT {key}_CLIENT_SECRET={csec}")
    else: print("  provider existed; client secret unchanged")

def cmd_proxy(a):
    prov = ak.one("/providers/proxy/", name=a.slug)
    # exact list, NOT a prefix match: Authentik 2026.x also ships 'bound_key' (needs DPoP -> authorize fails with
    # "dpop_jkt is required when bound_key scope is requested"), DCR and API-access mappings that must not be on a proxy provider
    want = ("authentik default OAuth Mapping: Proxy outpost", "authentik default OAuth Mapping: OpenID 'openid'", "authentik default OAuth Mapping: OpenID 'email'",
            "authentik default OAuth Mapping: OpenID 'profile'", "authentik default OAuth Mapping: Application Entitlements")
    mappings = [m["pk"] for m in ak.results("/propertymappings/all/") if m["name"] in want]
    body = {"name": a.slug, "authorization_flow": ak.flow(FLOW_AUTHZ)["pk"], "invalidation_flow": ak.flow(FLOW_INVAL)["pk"], "property_mappings": mappings,
            "external_host": a.host, "mode": "forward_single", "skip_path_regex": "\n".join(a.skip or []),
            # Authorization-header interception (Basic auth with an app password) only where a native client needs it
            "intercept_header_auth": bool(a.header_auth),
            "access_token_validity": "hours=24", "refresh_token_validity": "days=30"}
    prov = ak.patch(f"/providers/proxy/{prov['pk']}/", {k: body[k] for k in ("mode", "skip_path_regex", "external_host", "intercept_header_auth")}) if prov else ak.post("/providers/proxy/", body)
    o = ak.embedded_outpost(); provs = sorted(set(o["providers"] + [prov["pk"]]))
    if provs != sorted(o["providers"]): ak.patch(f"/outposts/instances/{o['pk']}/", {"providers": provs})
    app, g = ensure_app(a.slug, a.name or a.slug, prov["pk"], a.launch or a.host + "/", users_arg(a.users, env.get("ADMIN_USER") or "all"))
    print(f"proxy app {a.slug}: external host {a.host}, skip {a.skip or '-'}, header auth {'on' if a.header_auth else 'off'}; on the embedded outpost; group {g['name']}")

def cmd_user(a):
    if a.action == "add":
        if a.password: sys.exit("--password is gone (it put the password on the command line); use $HEARDOS_PASSWORD_FILE or --password-stdin")
        u = ak.user(a.username)
        if not u:
            given = read_secret_input(a.password_stdin)
            u = ak.post("/core/users/", {"username": a.username, "name": a.name or a.username, "email": a.email, "is_active": True, "type": "internal"})
            pw = given or secrets.token_urlsafe(16) + "!"
            ak.post(f"/core/users/{u['pk']}/set_password/", {"password": pw}); print(f"user {a.username} created")
            if not given: hand_back_secret(f"PERSON_{a.username.upper()}_FIRST_PASSWORD", pw)
        else: print(f"user {a.username} exists")
        if a.admin:   # admins always get MFA: authentik Admins (policy mfa-required-for-admins) + flow-mfa-required
            for name in ("authentik Admins", "flow-mfa-required"):
                g = ak.group(name)
                if not g: print(f"  warning: group {name} missing (run authentik/business-flows.py)"); continue
                ak.post(f"/core/groups/{g['pk']}/add_user/", {"pk": u["pk"]}); print(f"  {a.username} -> {name}")
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

AUTO = "heardos-automation"   # service account + group used by every kit script instead of akadmin's bootstrap token

def cmd_token(a):
    if a.action == "status":
        for t in ak.results("/core/tokens/?intent=api"):
            if t["identifier"].startswith(AUTO + "-") or t["identifier"] == "authentik-bootstrap-token":
                print(f"{t['identifier']}: expires {t.get('expires') if t.get('expiring') else 'never'}")
        return
    if a.action == "revoke-bootstrap":
        t = ak.one("/core/tokens/", identifier="authentik-bootstrap-token")
        if not t: print("bootstrap token already gone"); return
        if ak.get("/core/tokens/authentik-bootstrap-token/view_key/").get("key") == ak.tok:
            sys.exit("AUTHENTIK_TOKEN is still the bootstrap token; run `token rotate --write-env` first")
        ak.delete("/core/tokens/authentik-bootstrap-token/"); print("deleted authentik-bootstrap-token (akadmin)")
        return
    # rotate: superuser group (the kit edits flows, stages, policies, brands, providers, outposts, users and tokens;
    # Authentik RBAC has no practical narrower role for that), service-account user, fresh expiring API token
    g = ak.group(AUTO) or ak.post("/core/groups/", {"name": AUTO, "is_superuser": True, "users": [], "attributes": {"notes": "HeardOS automation service account (42-ak-app.py token rotate)."}})
    if not g.get("is_superuser"): ak.patch(f"/core/groups/{g['pk']}/", {"is_superuser": True})
    u = ak.user(AUTO) or ak.post("/core/users/", {"username": AUTO, "name": "HeardOS automation", "type": "service_account", "is_active": True, "groups": [g["pk"]]})
    if u.get("type") != "service_account": sys.exit(f"user {AUTO} exists but is not a service account; rename it first")
    if g["pk"] not in u.get("groups", []): ak.post(f"/core/groups/{g['pk']}/add_user/", {"pk": u["pk"]})
    ident = f"{AUTO}-{time.strftime('%Y%m%d%H%M%S')}"
    exp = (datetime.now(timezone.utc) + timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ak.post("/core/tokens/", {"identifier": ident, "intent": "api", "user": u["pk"], "expiring": True, "expires": exp,
                              "description": "HeardOS automation; rotate with 42-ak-app.py token rotate"})
    key = ak.get(f"/core/tokens/{ident}/view_key/")["key"]
    me = AK({"AUTHENTIK_URL": env["AUTHENTIK_URL"], "AUTHENTIK_TOKEN": key}).get("/core/users/me/")["user"]["username"]
    if me != AUTO: sys.exit(f"new token authenticates as {me!r}, expected {AUTO}; not revoking anything")
    print(f"token {ident} for {AUTO}, expires {exp}")
    if a.write_env: set_env_value("AUTHENTIK_TOKEN", key); print("  AUTHENTIK_TOKEN updated in the plan file")
    print(f"HEARDOS_OUTPUT AUTHENTIK_TOKEN={key}")
    if not a.keep_old:
        for t in ak.results("/core/tokens/?intent=api"):
            if t["identifier"].startswith(AUTO + "-") and t["identifier"] != ident and t.get("user") == u["pk"]:
                ak.delete(f"/core/tokens/{t['identifier']}/"); print(f"  revoked old token {t['identifier']}")

p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
o = sub.add_parser("oidc"); o.add_argument("slug"); o.add_argument("--name"); o.add_argument("--launch"); o.add_argument("--redirect", action="append", required=True); o.add_argument("--users"); o.add_argument("--out"); o.add_argument("--public", action="store_true", help="public PKCE client (needed when a native app does the code exchange itself, e.g. Swift Paperless); the web app may still send its secret"); o.set_defaults(f=cmd_oidc)
x = sub.add_parser("proxy"); x.add_argument("slug"); x.add_argument("--name"); x.add_argument("--host", required=True); x.add_argument("--skip", action="append"); x.add_argument("--launch"); x.add_argument("--users"); x.add_argument("--header-auth", action="store_true", help="let the outpost authenticate Authorization: Basic headers (app passwords) for native clients"); x.set_defaults(f=cmd_proxy)
u = sub.add_parser("user"); u.add_argument("action", choices=["add", "access", "revoke", "list", "join", "leave", "welcome", "suspend", "resume"]); u.add_argument("username", nargs="?", default=""); u.add_argument("slugs", nargs="?"); u.add_argument("--email"); u.add_argument("--name"); u.add_argument("--password", help=argparse.SUPPRESS); u.add_argument("--password-stdin", action="store_true"); u.add_argument("--admin", action="store_true"); u.set_defaults(f=cmd_user)
t = sub.add_parser("token"); t.add_argument("action", choices=["rotate", "revoke-bootstrap", "status"]); t.add_argument("--days", type=int, default=90); t.add_argument("--write-env", action="store_true", help="also replace AUTHENTIK_TOKEN in the plan file ($HEARDOS_ENV)"); t.add_argument("--keep-old", action="store_true"); t.set_defaults(f=cmd_token)
a = p.parse_args(); a.f(a)
