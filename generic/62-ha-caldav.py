#!/usr/bin/env python3
"""62-ha-caldav.py — add Home Assistant's CalDAV integration pointing at Baïkal as the read-only 'homeassistant' user.
Needs HA_TOKEN (a long-lived access token from an HA admin profile) in the plan file, and the Baïkal user to exist:
   host# 60-baikal.py user homeassistant ha@DOMAIN "Home Assistant" <password>   (then share every calendar to it as ro)
usage: 62-ha-caldav.py <baikal password for user homeassistant>"""
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ak import load_env
env = load_env(); HA = f"http://{env['HOST_LAN_IP']}:8209"; TOK = env.get("HA_TOKEN") or sys.exit("set HA_TOKEN in the plan file")
pw = sys.argv[1]
def api(method, path, data=None):
    r = urllib.request.Request(HA + path, method=method, data=json.dumps(data).encode() if data is not None else None,
                               headers={"Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as x: return json.load(x)
    except urllib.error.HTTPError as e: raise SystemExit(f"HA {method} {path}: {e.code} {e.read().decode()[:300]}")
if any(e.get("domain") == "caldav" for e in api("GET", "/api/config/config_entries/entry")):
    print("caldav integration already configured; reload it from Settings > Integrations to pick up new calendars"); sys.exit(0)
flow = api("POST", "/api/config/config_entries/flow", {"handler": "caldav", "show_advanced_options": True})
res = api("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {"url": "http://baikal/dav.php/", "username": "homeassistant", "password": pw, "verify_ssl": False})
print("result:", res.get("type"), res.get("title") or res.get("reason") or res.get("errors"))
ents = [s["entity_id"] for s in api("GET", "/api/states") if s["entity_id"].startswith(("calendar.", "todo."))]
print("calendar/todo entities now:", ents)
