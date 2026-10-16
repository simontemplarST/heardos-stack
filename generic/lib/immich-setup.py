#!/usr/bin/env python3
"""Immich post-install: admin sign-up (first run only) + OAuth/Authentik + external domain via /api/system-config.
usage: immich-setup.py <base_url> <admin_email> <admin_password> <client_id> <client_secret> <issuer> <public_url>"""
import json, sys, urllib.request, urllib.error
BASE, EMAIL, PW, CID, CSEC, ISSUER, PUB = sys.argv[1:8]
def api(method, path, data=None, token=None):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(BASE + path, method=method, data=json.dumps(data).encode() if data is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=60) as x: b = x.read(); return x.status, (json.loads(b) if b else {})
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:300]
st, r = api("POST", "/api/auth/admin-sign-up", {"email": EMAIL, "password": PW, "name": "Admin"})
print("admin sign-up:", st, "(400 = already initialised, fine)" if st == 400 else "")
st, login = api("POST", "/api/auth/login", {"email": EMAIL, "password": PW})
if st not in (200, 201): raise SystemExit(f"login failed: {st} {login}")
tok = login["accessToken"]
st, cfg = api("GET", "/api/system-config", token=tok)
cfg["oauth"].update({"enabled": True, "issuerUrl": ISSUER, "clientId": CID, "clientSecret": CSEC, "scope": "openid email profile",
                     "buttonText": "Sign in with HeardOS", "autoRegister": True, "autoLaunch": False,
                     "mobileOverrideEnabled": True, "mobileRedirectUri": PUB.rstrip("/") + "/api/oauth/mobile-redirect",
                     "signingAlgorithm": "RS256", "profileSigningAlgorithm": "none"})
cfg["passwordLogin"]["enabled"] = True
cfg["server"]["externalDomain"] = PUB.rstrip("/")
st, out = api("PUT", "/api/system-config", cfg, token=tok)
print("system-config:", st, {"oauth": out["oauth"]["enabled"], "externalDomain": out["server"]["externalDomain"]} if st == 200 else out)
