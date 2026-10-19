#!/usr/bin/env python3
"""cfdns.py [--delete [--apply]] — show photos.example.com's records; --delete removes only its CNAME to a
*.cfargotunnel.com target (the retired Cloudflare tunnel). Token/zone: see cfapi.py. Plan only without --apply."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cfapi import ARGS, ZONE, delete, done, records
NAME = "photos.example.com"
recs = records(NAME)
print("zone", ZONE, "| matches:", [(r["type"], r["name"], r["content"], r["proxied"], r["id"]) for r in recs])
if ARGS == ["--delete"]:
    for r in recs:
        if r["name"] == NAME and r["type"] == "CNAME" and r["content"].endswith(".cfargotunnel.com"): delete(r)
    print("after:" if "--apply" in sys.argv else "now:", [(r["type"], r["name"]) for r in records(NAME)])
    done()
