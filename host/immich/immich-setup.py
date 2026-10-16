#!/usr/bin/env python3
"""Immich post-install: admin sign-up (first run only), then OAuth (Authentik) + external domain via /api/system-config.
usage: immich-setup.py <base_url> <admin_email> <admin_password> <client_id> <client_secret>"""
import json, sys, urllib.request, urllib.error

BASE, EMAIL, PW, CID, CSEC = sys.argv[1:6]
ISSUER = "https://auth.example.com/application/o/immich/"

def api(method, path, data=None, token=None):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(BASE + path, method=method, data=json.dumps(data).encode() if data is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            b = x.read(); return x.status, (json.loads(b) if b else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

st, about = api("GET", "/api/server/about")
print("server:", st, about.get("version") if isinstance(about, dict) else about)

st, r = api("POST", "/api/auth/admin-sign-up", {"email": EMAIL, "password": PW, "name": "Alice Example"})
print("admin sign-up:", st, (r.get("email") if isinstance(r, dict) else r))   # 400 "already has an admin" on reruns is fine

st, login = api("POST", "/api/auth/login", {"email": EMAIL, "password": PW})
if st != 201 and st != 200: raise SystemExit(f"login failed: {st} {login}")
tok = login["accessToken"]; print("login ok, admin:", login.get("isAdmin"))

st, cfg = api("GET", "/api/system-config", token=tok)
if st != 200: raise SystemExit(f"get config failed {st} {cfg}")
cfg["oauth"].update({
    "enabled": True, "issuerUrl": ISSUER, "clientId": CID, "clientSecret": CSEC,
    "scope": "openid email profile", "buttonText": "Sign in with HeardOS",
    "autoRegister": True, "autoLaunch": False,
    "mobileOverrideEnabled": True, "mobileRedirectUri": "https://photos.example.com/api/oauth/mobile-redirect",
    "signingAlgorithm": "RS256", "profileSigningAlgorithm": "none",
})
cfg["passwordLogin"]["enabled"] = True            # keep as break-glass for the admin account
cfg["server"]["externalDomain"] = "https://photos.example.com"
st, out = api("PUT", "/api/system-config", cfg, token=tok)
print("system-config:", st, (out if st != 200 else {"oauth.enabled": out["oauth"]["enabled"], "issuer": out["oauth"]["issuerUrl"], "externalDomain": out["server"]["externalDomain"], "mobileRedirect": out["oauth"]["mobileRedirectUri"]}))

st, pub = api("GET", "/api/server/config")
print("public config:", st, {k: pub.get(k) for k in ("oauthButtonText", "isInitialized", "externalDomain")} if isinstance(pub, dict) else pub)
st, auth = api("POST", "/api/oauth/authorize", {"redirectUri": "https://photos.example.com/auth/login"})
print("oauth authorize url:", st, (auth.get("url", "")[:110] if isinstance(auth, dict) else auth))
