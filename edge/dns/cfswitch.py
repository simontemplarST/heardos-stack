#!/usr/bin/env python3
"""cfswitch.py LIGHTHOUSE_IP "host1 host2 ..." [--apply] — point <host>.example.com at the lighthouse: its CNAME/A/AAAA
records are replaced by one unproxied A record (other types untouched). Prints the plan; --apply makes it, logging
every deleted record as JSON. Token/zone: see cfapi.py."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cfapi import APPLY, ARGS, create, delete, done, records
LH, hosts = ARGS[0], ARGS[1].split()
recs = records()
tun = [r for r in recs if r["type"] == "CNAME" and r["content"].endswith(".cfargotunnel.com")]
print("tunnel CNAMEs in zone:", [(r["name"], r["proxied"]) for r in tun])
for h in hosts:
    name = f"{h}.example.com"; cur = [r for r in recs if r["name"] == name]
    print(f"  {name}: " + ", ".join(f'{r["type"]} {r["content"][:40]} proxied={r["proxied"]}' for r in cur))
    if any(r["type"] == "A" and r["content"] == LH and not r["proxied"] for r in cur): print("    already ok"); continue
    for r in cur:
        if r["type"] in ("CNAME", "A", "AAAA"): delete(r)
    create({"type": "A", "name": name, "content": LH, "ttl": 300, "proxied": False, "comment": "nebula lighthouse ingress 2026-10-11"})
if APPLY:
    after = records()
    print("after:", [(r["name"].split(".")[0], r["type"], r["content"], r["proxied"]) for r in after if r["name"].split(".")[0] in hosts])
done()
