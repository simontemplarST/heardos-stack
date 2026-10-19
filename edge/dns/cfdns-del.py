#!/usr/bin/env python3
"""cfdns-del.py NAME [NAME...] [--apply] — delete the unproxied A record <name>.example.com -> 203.0.113.10 (that type and
target only; anything else on the name stays). Token/zone: see cfapi.py. Plan only without --apply; deletions are logged as JSON."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cfapi import ARGS, delete, done, fq, records
LH = "203.0.113.10"
for n in ARGS:
    name = fq(n)
    for rec in records(name, "A"):
        if rec["content"] == LH: delete(rec)
    print(name, "remaining:" if "--apply" in sys.argv else "current:", [(r["type"], r["content"]) for r in records(name)])
done()
