#!/usr/bin/env python3
"""Runtipi API helper: tipi.py login|installed|install URN JSON|restart URN|stop URN|start URN|status URN
Credentials from env RUNTIPI_ADMIN_USER / RUNTIPI_ADMIN_PASSWORD."""
import json, os, sys, urllib.request, http.cookiejar
BASE="http://192.168.10.20"; H={"Content-Type":"application/json","Origin":"http://192.168.10.20","Referer":"http://192.168.10.20/"}
cj=http.cookiejar.CookieJar(); op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def call(m,p,d=None):
    r=urllib.request.Request(BASE+p,method=m,data=json.dumps(d).encode() if d is not None else None,headers=H)
    try:
        with op.open(r,timeout=120) as x: b=x.read(); return x.status,(json.loads(b) if b else {})
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:500]
s,_=call("POST","/api/auth/login",{"username":os.environ["RUNTIPI_ADMIN_USER"],"password":os.environ["RUNTIPI_ADMIN_PASSWORD"]})
assert s in (200,201),("login failed",s)
cmd=sys.argv[1]
if cmd=="installed":
    s,d=call("GET","/api/apps/installed")
    for a in d["installed"]: print(a["app"]["id"], a["info"]["id"]+":"+a["info"].get("storeId","?") if "info" in a else "", a["app"]["status"], a["app"].get("port"), a["app"].get("localSubdomain"))
elif cmd=="install":
    urn=sys.argv[2]; body=json.loads(sys.argv[3]); print(call("POST",f"/api/app-lifecycle/{urn}/install",body))
elif cmd in ("restart","stop","start"):
    print(call("POST",f"/api/app-lifecycle/{sys.argv[2]}/{cmd}",{}))
elif cmd=="status":
    s,d=call("GET","/api/apps/installed")
    for a in d["installed"]:
        if sys.argv[2] in (str(a["app"]["id"]), a["app"].get("appName",""), (a.get("info",{}).get("id","")+":"+a.get("info",{}).get("storeId",""))): print(json.dumps(a["app"],indent=1)[:1500])
elif cmd=="raw":
    print(json.dumps(call(sys.argv[2],sys.argv[3],json.loads(sys.argv[4]) if len(sys.argv)>4 else None),indent=1)[:4000])
