#!/usr/bin/env python3
"""opencloud-authentik.py — Authentik OIDC for OpenCloud at https://docs.$DOMAIN (run on the host; plan file via HEARDOS_ENV).
Per integrations.goauthentik.io (OpenCloud): four PUBLIC clients with the fixed ids the OpenCloud clients expect (web,
OpenCloudDesktop, OpenCloudAndroid, OpenCloudIOS), issuer_mode global (issuer https://auth.$DOMAIN/ — Authentik has no root
discovery doc, so the lighthouse Caddy rewrites /.well-known/openid-configuration to the `opencloud` provider's), the same signing
key, and a `roles` claim (scope profile): "authentik Admins" -> opencloudAdmin, everyone else opencloudUser.
App `opencloud` (visible, launches cloud.$DOMAIN/docs) + hidden opencloud-desktop/-android/-ios, all bound to group app-opencloud.
  opencloud-authentik.py [--users a,b,c] [--extra-host https://opencloud.example]   (--users ADDS members; idempotent)
  opencloud-authentik.py --print-people   -> "username:Display Name:mail,..." for every app-opencloud member (for apps/opencloud/setup.py)"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ak import AK, load_env
ap = argparse.ArgumentParser(); ap.add_argument("--users", default=""); ap.add_argument("--extra-host", action="append", default=[])
ap.add_argument("--print-people", action="store_true")
a = ap.parse_args(); env = load_env(); ak = AK(env); D = env["DOMAIN"]
if a.print_people:
    g = ak.group("app-opencloud") or {}
    us = [u for u in ak.results("/core/users/") if u["pk"] in set(g.get("users") or [])]
    print(",".join(f"{u['username']}:{u['name'].replace(':', ' ').replace(',', ' ')}:{u.get('email') or ''}" for u in sorted(us, key=lambda u: u["username"])))
    sys.exit(0)
hosts = [f"https://docs.{D}"] + a.extra_host
key = next(k["pk"] for k in ak.results("/crypto/certificatekeypairs/?has_key=true") if "authentik Self-signed" in k["name"])
expr = "admin = ak_is_group_member(request.user, name='authentik Admins')\nreturn {'roles': ['opencloudAdmin' if admin else 'opencloudUser']}"
rm = ak.one("/propertymappings/provider/scope/", name="heardos-opencloud-roles")
body = {"name": "heardos-opencloud-roles", "scope_name": "profile", "description": "OpenCloud role", "expression": expr}
rm = ak.patch(f"/propertymappings/provider/scope/{rm['pk']}/", body) if rm else ak.post("/propertymappings/provider/scope/", body)
def m(n): return ak.mapping(n)["pk"]
email = ak.mapping("HeardOS email (verified)") or ak.mapping("authentik default OAuth Mapping: OpenID 'email'")
base = [m("authentik default OAuth Mapping: OpenID 'openid'"), m("authentik default OAuth Mapping: OpenID 'profile'"), email["pk"], rm["pk"]]
off = m("authentik default OAuth Mapping: OpenID 'offline_access'")
web = [("strict", h + s) for h in hosts for s in ("/oidc-callback.html", "/oidc-silent-redirect.html", "/")]
clients = [("opencloud", "Documents (OpenCloud)", "web", web, False),
           ("opencloud-desktop", "OpenCloud desktop", "OpenCloudDesktop", [("regex", r"http://127\.0\.0\.1(:[0-9]+)?(/.*)?"), ("regex", r"http://localhost(:[0-9]+)?(/.*)?")], True),
           ("opencloud-android", "OpenCloud Android", "OpenCloudAndroid", [("strict", "oc://android.opencloud.eu")], True),
           ("opencloud-ios", "OpenCloud iOS", "OpenCloudIOS", [("strict", "oc://ios.opencloud.eu")], True)]
g = ak.ensure_group("app-opencloud", [ak.user(env.get("ADMIN_USER", "admin"))["pk"]], "Access to application 'OpenCloud'. Membership = access.")
want = {ak.user(u)["pk"] for u in filter(None, a.users.split(","))} | set(g.get("users") or [])
if want != set(g.get("users") or []):
    g = ak.patch(f"/core/groups/{g['pk']}/", {"users": sorted(want)}); print("app-opencloud members:", len(want))
for slug, name, cid, uris, offline in clients:
    body = {"name": slug, "authorization_flow": ak.flow("default-provider-authorization-implicit-consent")["pk"],
            "invalidation_flow": ak.flow("default-provider-invalidation-flow")["pk"], "client_type": "public", "client_id": cid,
            "signing_key": key, "issuer_mode": "global", "sub_mode": "hashed_user_id", "include_claims_in_id_token": True,
            "property_mappings": base + ([off] if offline else []), "access_code_validity": "minutes=1", "access_token_validity": "minutes=30",
            "refresh_token_validity": "days=30", "grant_types": ["authorization_code", "refresh_token"],
            "redirect_uris": [{"matching_mode": mm, "url": u} for mm, u in uris]}
    if slug == "opencloud": body |= {"logout_uri": hosts[0] + "/backchannel_logout", "logout_method": "backchannel"}
    p = ak.one("/providers/oauth2/", name=slug)
    try: p = ak.patch(f"/providers/oauth2/{p['pk']}/", body) if p else ak.post("/providers/oauth2/", body)
    except Exception as e:
        body.pop("logout_uri", None); body.pop("logout_method", None); print(f"  {slug}: retry without backchannel logout ({str(e)[:120]})")
        p = ak.patch(f"/providers/oauth2/{p['pk']}/", body) if p else ak.post("/providers/oauth2/", body)
    launch = f"https://cloud.{D}/docs" if slug == "opencloud" else "blank://blank"
    app = ak.one("/core/applications/?superuser_full_list=true", slug=slug)   # without the flag the list hides apps the token user cannot launch
    if app: ak.patch(f"/core/applications/{slug}/", {"name": name, "provider": p["pk"], "meta_launch_url": launch})
    else: app = ak.post("/core/applications/", {"name": name, "slug": slug, "provider": p["pk"], "meta_launch_url": launch, "policy_engine_mode": "any"})
    ak.ensure_binding(app["pk"], group=g["pk"])
    print(f"{slug}: provider {p['pk']} client_id {cid}")
