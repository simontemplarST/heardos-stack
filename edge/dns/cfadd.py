#!/usr/bin/env python3
"""cfadd.py [--apply] — the 2026-10-11 one-off: unproxied A photos.example.com -> the Nebula lighthouse (203.0.113.10).
Token/zone: see cfapi.py (CF_API_TOKEN, zone-scoped). Plan only without --apply."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cfapi import create, done, records
NAME, LH = "photos.example.com", "203.0.113.10"
ex = records(NAME)
print("existing:", [(r["type"], r["content"]) for r in ex])
if not any(r["type"] == "A" and r["content"] == LH for r in ex):
    create({"type": "A", "name": NAME, "content": LH, "ttl": 300, "proxied": False, "comment": "nebula lighthouse ingress 2026-10-11"})
done()
