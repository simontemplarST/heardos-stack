#!/usr/bin/env python3
"""cfdns-txt.py NAME 'content' [NAME 'content' ...] [--apply] — create a TXT record in example.com, or replace the one
with the same tag (v=spf1, v=DMARC1, ...); the replaced record is printed as JSON first.
   cfdns-txt.py --delete-a NAME [TARGET] [--apply] — delete NAME's A records (only the one pointing at TARGET when given),
   e.g. a dangling host. Token/zone: see cfapi.py. Plan only without --apply."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cfapi import ARGS, delete, done, fq, records, replace, create
if ARGS and ARGS[0] == "--delete-a":
    if len(ARGS) < 2: raise SystemExit(__doc__)
    name, target = fq(ARGS[1]), (ARGS[2] if len(ARGS) > 2 else None)
    hit = [r for r in records(name, "A") if target is None or r["content"] == target]
    if not hit: print(name, "no matching A record")
    for rec in hit: delete(rec)
    done(); sys.exit()
tag = lambda c: c.strip('"').split(";")[0].split("=")[0].strip().lower()
for n, content in zip(ARGS[::2], ARGS[1::2]):
    name = fq(n); ex = records(name, "TXT")
    if [r for r in ex if r["content"].strip('"') == content]: print(name, "TXT already present"); continue
    same = [r for r in ex if tag(r["content"]) == tag(content)]
    if same: replace(same[0], {"type": "TXT", "name": name, "content": content, "ttl": 300})
    else: create({"type": "TXT", "name": name, "content": content, "ttl": 300, "comment": "security baseline 2026-10-15"})
done()
