#!/usr/bin/env python3
"""cfdns-add.py NAME [NAME...] [--apply] — create unproxied A records <name>.example.com -> Nebula lighthouse (203.0.113.10).
Token/zone: see cfapi.py (CF_API_TOKEN, zone-scoped). Only adds; plan only without --apply."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cfapi import ARGS, create, done, fq, records
LH = "203.0.113.10"
for n in ARGS:
    name = fq(n); ex = records(name)
    print(name, "existing:", [(r["type"], r["content"], r["proxied"]) for r in ex])
    if not any(r["type"] == "A" and r["content"] == LH for r in ex):
        create({"type": "A", "name": name, "content": LH, "ttl": 300, "proxied": False, "comment": "nebula lighthouse ingress 2026-10-12"})
done()
